"""Register a test ticket as a category card (objective 3).

Since 12 Nov 2026 the booking queue requests codes only for Sure Odds tickets and category
cards; a category card is an accumulator ticket registered in sports_ai_high_odds_tickets.
"""
import json


def register_category_card(connection, ticket_id, category_id='5-9.99', now=None):
    moment = (now.isoformat() if hasattr(now, 'isoformat') else str(now or '2026-11-12T00:00:00+00:00'))
    connection.execute(
        """INSERT OR IGNORE INTO sports_ai_high_odds_events(id,event_key,event_type,payload_json,status,available_at,created_at)
           VALUES('test-event','test-event','test','{}','completed',?,?)""",
        (moment, moment),
    )
    connection.execute(
        """INSERT OR IGNORE INTO sports_ai_high_odds_tickets
           (ticket_id,category_id,event_id,signature,snapshot_json,snapshot_hash,confidence,lower_joint_probability,risk,
            registration_state,created_at,updated_at)
           VALUES(?,?,'test-event',?,?,'hash',0.8,0.2,0.8,'pending',?,?)""",
        (ticket_id, category_id, f'test-card|{ticket_id}', json.dumps({'legs': []}), moment, moment),
    )


def unregister_category_card(connection, ticket_id):
    connection.execute('DELETE FROM sports_ai_high_odds_tickets WHERE ticket_id=?', (ticket_id,))
