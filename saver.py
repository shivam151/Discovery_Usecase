import sqlite3
import pandas as pd

conn = sqlite3.connect('discovery.db')

df = pd.read_sql_query("SELECT * FROM questions", conn)
df.to_csv('questions_saved.csv', index=False)