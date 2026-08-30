def get_top_5_leaderboard():
    """Recupera top 5 utenti per spiritelli masterati"""
    try:
        conn = get_db_connection()
        if not conn:
            return []
        
        c = conn.cursor()
        c.execute("""
            SELECT 
                c.user_id,
                u.username,
                COUNT(*) as totali,
                SUM(CASE WHEN c.mastered = true THEN 1 ELSE 0 END) as masterati
            FROM collezione c
            LEFT JOIN utenti u ON c.user_id = u.user_id
            GROUP BY c.user_id, u.username
            ORDER BY masterati DESC
            LIMIT 5
        """)
        rows = c.fetchall()
        c.close()
        release_db_connection(conn)
        
        return [
            {
                "user_id": r[0],
                "username": r[1],
                "totali": r[2],
                "masterati": r[3]
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Errore get_top_5: {str(e)}")
        return []
