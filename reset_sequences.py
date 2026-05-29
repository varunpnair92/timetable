from django.db import connection

with connection.cursor() as cursor:
    # 1. Fetch all sequence names in the database
    cursor.execute("""
        SELECT c.relname 
        FROM pg_class c 
        JOIN pg_namespace n ON n.oid = c.relnamespace 
        WHERE c.relkind = 'S';
    """)
    sequences = [r[0] for r in cursor.fetchall()]
    
    print(f"Found sequences: {sequences}")
    
    for seq in sequences:
        if not seq.endswith('_seq'):
            continue
            
        parts = seq[:-4].split('_')
        if len(parts) < 2:
            continue
            
        col_name = parts[-1]
        possible_table = "_".join(parts[:-1])
        
        # Verify table and column exist in the database
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.columns 
                WHERE table_name = %s AND column_name = %s
            );
        """, [possible_table, col_name])
        exists = cursor.fetchone()[0]
        
        if not exists:
            continue
            
        # Query max ID
        cursor.execute(f'SELECT COALESCE(MAX("{col_name}"), 0) FROM "{possible_table}"')
        max_val = cursor.fetchone()[0]
        
        # Query current sequence value
        try:
            cursor.execute(f'SELECT last_value FROM "{seq}"')
            last_val = cursor.fetchone()[0]
        except Exception:
            last_val = "unknown"
            
        print(f"Sequence: {seq} | Table: {possible_table} | Max ID: {max_val} | Current Sequence: {last_val}")
        
        # Update sequence next value to max_val + 1
        next_val = max(max_val + 1, 1)
        cursor.execute(f"SELECT setval('\"{seq}\"', %s, false)", [next_val])
        print(f"  -> Successfully reset sequence '{seq}' to next value: {next_val}")

print("All sequences reset completed successfully!")
