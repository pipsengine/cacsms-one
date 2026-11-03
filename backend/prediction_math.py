"""Deterministic scoring math shared by training and inference.

No network access and no fabricated inputs. Missing numbers stay missing.
"""

import math


def logit(probability):
    probability = min(1 - 1e-6, max(1e-6, probability))
    return math.log(probability / (1 - probability))


def sigmoid(value):
    if value >= 0:
        z = math.exp(-value)
        return 1 / (1 + z)
    z = math.exp(value)
    return z / (1 + z)


def _solve(matrix, vector):
    size = len(vector)
    aug = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(aug[row][col]))
        if abs(aug[pivot][col]) < 1e-12:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        scale = aug[col][col]
        for col_index in range(col, size + 1):
            aug[col][col_index] /= scale
        for row in range(size):
            if row == col:
                continue
            factor = aug[row][col]
            for col_index in range(col, size + 1):
                aug[row][col_index] -= factor * aug[col][col_index]
    return [aug[row][size] for row in range(size)]


def fit_ridge(rows, columns, target, penalty=1.0):
    """Least squares with an L2 penalty. Returns coefficients or None."""
    usable = []
    for features, actual in rows:
        values = []
        valid = True
        for column in columns:
            if column == 'intercept':
                values.append(1.0)
            else:
                value = features.get(column)
                if value is None or not math.isfinite(value):
                    valid = False
                    break
                values.append(float(value))
        if valid and actual is not None and math.isfinite(actual):
            usable.append((values, float(actual)))
    if len(usable) < max(8, len(columns) + 2):
        return None
    width = len(columns)
    gram = [[0.0 for _ in range(width)] for _ in range(width)]
    moment = [0.0 for _ in range(width)]
    for values, actual in usable:
        for i in range(width):
            moment[i] += values[i] * actual
            for j in range(width):
                gram[i][j] += values[i] * values[j]
    for i in range(width):
        if columns[i] != 'intercept':
            gram[i][i] += penalty
    coefficients = _solve(gram, moment)
    if not coefficients or not all(math.isfinite(value) for value in coefficients):
        return None
    return {'columns': columns, 'coefficients': coefficients, 'sample_size': len(usable), 'penalty': penalty}


def predict_ridge(model, features):
    if not model:
        return None
    total = 0.0
    for column, coefficient in zip(model['columns'], model['coefficients']):
        value = 1.0 if column == 'intercept' else features.get(column)
        if value is None or not math.isfinite(value):
            return None
        total += coefficient * float(value)
    return total if math.isfinite(total) else None


def _stump_fit(samples, feature):
    values = sorted({sample[0][feature] for sample in samples})
    if len(values) < 2:
        return None
    best = None
    candidates = values[1:-1] if len(values) > 4 else values[1:]
    if not candidates:
        return None
    step = max(1, len(candidates) // 8)
    for threshold in candidates[::step]:
        left = [residual for features, residual in samples if features[feature] <= threshold]
        right = [residual for features, residual in samples if features[feature] > threshold]
        if len(left) < 5 or len(right) < 5:
            continue
        left_mean = sum(left) / len(left)
        right_mean = sum(right) / len(right)
        loss = sum((value - left_mean) ** 2 for value in left) + sum((value - right_mean) ** 2 for value in right)
        if best is None or loss < best['loss']:
            best = {
                'feature': feature, 'threshold': threshold, 'left': left_mean,
                'right': right_mean, 'loss': loss,
            }
    return best


def fit_gradient_boosting(rows, feature_names, target_home, target_away, baseline_home, baseline_away, rounds=20, learning_rate=0.1):
    """Shallow residual booster. Trains only when every row has the requested features."""
    if len(rows) < 80:
        return None
    prepared = []
    for features, home_actual, away_actual in rows:
        if any(features.get(name) is None or not math.isfinite(features.get(name)) for name in feature_names):
            return None
        prepared.append((features, float(home_actual), float(away_actual)))
    if len(prepared) < 80:
        return None
    home_pred = [float(features[baseline_home]) for features, _, _ in prepared]
    away_pred = [float(features[baseline_away]) for features, _, _ in prepared]
    home_stumps, away_stumps = [], []
    for _ in range(rounds):
        for stumps, predictions, actual_index in (
            (home_stumps, home_pred, 1), (away_stumps, away_pred, 2),
        ):
            samples = [
                (row[0], row[actual_index] - prediction)
                for row, prediction in zip(prepared, predictions)
            ]
            best = None
            for feature in feature_names:
                stump = _stump_fit(samples, feature)
                if stump and (best is None or stump['loss'] < best['loss']):
                    best = stump
            if not best:
                return None
            best.pop('loss', None)
            stumps.append(best)
            for index, row in enumerate(prepared):
                value = row[0][best['feature']]
                predictions[index] += learning_rate * (best['left'] if value <= best['threshold'] else best['right'])
    return {
        'feature_names': feature_names,
        'baseline_home': baseline_home,
        'baseline_away': baseline_away,
        'learning_rate': learning_rate,
        'home_stumps': home_stumps,
        'away_stumps': away_stumps,
        'sample_size': len(prepared),
    }


def predict_gradient_boosting(model, features, floor, ceiling):
    if not model:
        return None, None
    if any(features.get(name) is None for name in model['feature_names']):
        return None, None
    home = float(features[model['baseline_home']])
    away = float(features[model['baseline_away']])
    rate = model['learning_rate']
    for stump in model['home_stumps']:
        home += rate * (stump['left'] if features[stump['feature']] <= stump['threshold'] else stump['right'])
    for stump in model['away_stumps']:
        away += rate * (stump['left'] if features[stump['feature']] <= stump['threshold'] else stump['right'])
    if not math.isfinite(home) or not math.isfinite(away):
        return None, None
    return min(ceiling, max(floor, home)), min(ceiling, max(floor, away))


def fit_isotonic(pairs):
    blocks = []
    for probability, outcome in sorted(pairs, key=lambda item: item[0]):
        blocks.append([float(outcome), 1.0, float(probability), float(probability)])
        while len(blocks) >= 2:
            left, right = blocks[-2], blocks[-1]
            if left[0] / left[1] <= right[0] / right[1] + 1e-12:
                break
            merged = [left[0] + right[0], left[1] + right[1], left[2], right[3]]
            blocks[-2:] = [merged]
    return [
        {'lower': block[2], 'upper': block[3], 'prediction': block[0] / block[1]}
        for block in blocks
    ]


def apply_isotonic(blocks, probability):
    if not blocks:
        return probability
    if probability <= blocks[0]['lower']:
        return blocks[0]['prediction']
    if probability >= blocks[-1]['upper']:
        return blocks[-1]['prediction']
    for block in blocks:
        if block['lower'] <= probability <= block['upper']:
            return block['prediction']
    return blocks[-1]['prediction']


def fit_sigmoid(pairs):
    """Grid-searched Platt scaling on logit(probability)."""
    if len(pairs) < 8:
        return None
    best, best_loss = {'a': 1.0, 'b': 0.0}, float('inf')
    for a_step in range(5, 21):
        a = a_step / 10
        for b_step in range(-10, 11):
            b = b_step / 10
            loss = 0.0
            for probability, outcome in pairs:
                estimate = min(1 - 1e-6, max(1e-6, sigmoid(a * logit(probability) + b)))
                loss -= outcome * math.log(estimate) + (1 - outcome) * math.log(1 - estimate)
            if loss < best_loss:
                best, best_loss = {'a': a, 'b': b}, loss
    return best


def apply_sigmoid(model, probability):
    if not model:
        return probability
    return min(1 - 1e-6, max(1e-6, sigmoid(model['a'] * logit(probability) + model['b'])))


def _lcg(seed):
    state = seed % 2147483647 or 1

    def uniform():
        nonlocal state
        state = (16807 * state) % 2147483647
        return (state - 1) / 2147483646

    return uniform


def _normal_pair(uniform):
    u1 = max(1e-12, uniform())
    u2 = uniform()
    radius = math.sqrt(-2 * math.log(u1))
    return radius * math.cos(2 * math.pi * u2), radius * math.sin(2 * math.pi * u2)


def simulate_scores(home_mean, away_mean, home_sd, away_sd, draws=4000, seed=1):
    uniform = _lcg(seed)
    scores = []
    for _ in range(draws // 2):
        z_home, z_away = _normal_pair(uniform)
        scores.append((home_mean + home_sd * z_home, away_mean + away_sd * z_away))
        z_home, z_away = _normal_pair(uniform)
        scores.append((home_mean + home_sd * z_home, away_mean + away_sd * z_away))
    return scores[:draws]


def monte_carlo_settlement(samples, settle):
    result = {'p_win': 0.0, 'p_half_win': 0.0, 'p_push': 0.0, 'p_half_loss': 0.0, 'p_loss': 0.0}
    keys = {1: 'p_win', .5: 'p_half_win', 0: 'p_push', -.5: 'p_half_loss', -1: 'p_loss'}
    counted = 0
    for home, away in samples:
        outcome = settle(home, away)
        key = keys.get(outcome)
        if key:
            result[key] += 1
            counted += 1
    if not counted:
        return None
    return {key: value / counted for key, value in result.items()}
