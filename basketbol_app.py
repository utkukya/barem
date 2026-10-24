"""
Basketbol istatistik ve barem arayüzü (futboldan ayrı uygulama, ayrı veritabanı: basketbol.db).
Çalıştırma:  streamlit run basketbol_app.py --server.port 8502
"""
import streamlit as st

st.set_page_config(page_title="Basketbol · Maç İstatistik", page_icon="🏀", layout="wide")

sayfa = st.navigation([st.Page("basketbol_sayfa.py", title="Basketbol", icon="🏀", default=True)],
                      position="hidden")
sayfa.run()
