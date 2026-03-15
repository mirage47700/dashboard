#!/usr/bin/env python3
"""
Script pour ajouter la table missing 'heartbeats' dans la base de données du dashboard
"""

import sqlite3
from pathlib import Path

# Chemin vers la base de données dans le container Docker
# Le volume est monté depuis ~/dashboard/data/dashboard.db
DB_PATH = Path('/Users/juliendacostafreitas/dashboard/data/dashboard.db')

def add_heartbeats_table():
    """Créer la table heartbeats si elle n'existe pas"""
    
    if not DB_PATH.exists():
        print(f"❌ Base de données non trouvée : {DB_PATH}")
        return False
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Créer la table heartbeats
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS heartbeats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                status TEXT DEFAULT 'ok',
                message TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        
        # Créer l'index séparément
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_heartbeats_timestamp ON heartbeats(timestamp)
        """)
        
        conn.commit()
        print("✅ Table `heartbeats` créée avec succès !")
        
        # Vérifier que la table existe maintenant
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='heartbeats';")
        result = cursor.fetchone()
        
        if result:
            print("✅ Vérification OK - La table heartbeats existe maintenant !")
        else:
            print("❌ Erreur - Table heartbeats non trouvée après création")
            
        conn.close()
        return True
        
    except sqlite3.Error as e:
        print(f"❌ Erreur SQLite : {e}")
        return False

if __name__ == "__main__":
    print("🔧 Ajout de la table heartbeats...")
    print("=" * 60)
    
    success = add_heartbeats_table()
    
    if success:
        print("\n✅ Opération terminée !")
        print("   Redémarre le container Docker pour appliquer les changements:")
        print("   docker restart dashboard")
    else:
        print("\n⚠️ L'opération a échoué. Vérifie les permissions ou l'emplacement de la base de données.")
