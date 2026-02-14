import sqlite3

def add_leader_id_to_leads():
    try:
        # Connect to your database file
        conn = sqlite3.connect('leafplant.db')
        cursor = conn.cursor()

        print("Adding leader_id column to WhatsAppLead...")
        
        # SQLite command to add the column
        # We set it to INTEGER and allow NULLs as per your model
        cursor.execute('ALTER TABLE whats_app_lead ADD COLUMN leader_id INTEGER REFERENCES group_leader(id)')

        conn.commit()
        conn.close()
        print("Success! The column has been added without data loss.")
        
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("Look like the column already exists!")
        else:
            print(f"Operational Error: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    add_leader_id_to_leads()