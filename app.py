import streamlit as st
import pandas as pd
import re
from io import StringIO, BytesIO
import time
from datetime import datetime
import calendar
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

st.set_page_config(page_title="Extractor Sunat - Prolan", page_icon="📊", layout="centered")
st.title("📊 Extractor Automático de Exportaciones")

def _contar_duas(t):
    if t is None or t.empty:
        return 0
    return int(t.iloc[:, 0].astype(str).str.contains(r'\d{3}-\d{4}-\d+', regex=True).sum() +
               t.iloc[:, 1].astype(str).str.contains(r'\d{3}-\d{4}-\d+', regex=True).sum())

def _extraer_mejor_tabla(html):
    try:
        tablas = pd.read_html(StringIO(html))
    except ValueError:
        return None
    best_t, max_duas = None, -1
    # Escaneamos el HTML buscando la tabla real de exportaciones
    for t in tablas:
        if t.shape[1] >= 15:
            duas = _contar_duas(t)
            if duas > max_duas:
                max_duas = duas
                best_t = t.copy()
    if best_t is not None:
        best_t.columns = range(best_t.shape[1])
    return best_t

@st.cache_data(show_spinner=False)
def extraer_datos_aduanet_vista_unica(fecha_inicio, fecha_fin, ruc):
    """Extrae todos los datos asumiendo que Aduanet los renderiza en una sola vista sin paginación."""
    opciones = Options()
    opciones.add_argument("--headless")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

    opciones.binary_location = "/usr/bin/chromium"
    servicio = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=servicio, options=opciones)

    url_busqueda = f"http://www.aduanet.gob.pe/cl-ad-consdespade/ConsExportIAServlet?accion=infDeta&FecInicial={fecha_inicio}&FecFinal={fecha_fin}&codseleccion=exportador&dato={ruc}&flagBusq=1&pTipoConsulta=infDeta"
    
    try:
        driver.get(url_busqueda)
        # Damos 5 segundos para que la tabla gigante (ej. Julio con 1389 filas) cargue por completo
        time.sleep(5) 
        html_final = driver.page_source
    finally:
        driver.quit() 

    # Buscamos cuántos registros dice la web que hay en total
    match_total = re.search(r"a\s+\d+\s+de\s+(\d+)", html_final)
    total_esperado = int(match_total.group(1)) if match_total else 0

    # Extraemos la tabla directamente de esta vista única
    df_crudo = _extraer_mejor_tabla(html_final)
    
    if df_crudo is None or df_crudo.empty:
        return pd.DataFrame(), total_esperado

    # --- LIMPIEZA MAESTRA ---
    # 1. Rellenamos las celdas combinadas (DUA y EXPORTADOR) hacia abajo para salvar los registros múltiples
    df_crudo[0] = df_crudo[0].replace([None, 'nan', 'NaN', ''], pd.NA).ffill()
    df_crudo[1] = df_crudo[1].replace([None, 'nan', 'NaN', ''], pd.NA).ffill()

    # 2. Conservamos SOLO las filas donde el ítem (SERIE) sea un número real
    if df_crudo.shape[1] >= 12:
        mask_serie = pd.to_numeric(df_crudo[11], errors='coerce').notna()
        df_final = df_crudo[mask_serie].reset_index(drop=True)
    else:
        df_final = df_crudo

    # 3. Formateamos las 22 columnas exactas
    cols = ['DESCLARACION', 'EXPORTADOR', 'FEC.NUM', 'AGENTE', "CANT SERIE'S", 'FOB TOT.', 'ALMACEN', 'AFORO', 'NETO TOT', '# BULTOS', 'PAIS DEST', 'SERIE', 'PARTIDA', 'DESC. COMER', 'DESC. PREST', 'DESC. MAT. CONST', 'DES. USO', 'DESC. OTROS', 'CANT', 'UNID.', 'PESO NETO', 'FOB']
    df_final = df_final.iloc[:, :22]
    df_final.columns = cols[:len(df_final.columns)]

    # 4. Forzamos la columna FOB a número decimal para tus reportes
    if 'FOB' in df_final.columns:
        df_final['FOB'] = df_final['FOB'].astype(str).str.replace(',', '', regex=False).str.strip()
        df_final['FOB'] = pd.to_numeric(df_final['FOB'], errors='coerce')
        
    return df_final, total_esperado


ruc_input = st.text_input("RUC de la empresa:", value="20451899881")
col1, col2 = st.columns(2)
with col1: fecha_input_inicio = st.text_input("Fecha Inicio (DDMMAAAA):", value="01012026")
with col2: fecha_input_fin = st.text_input("Fecha Fin (DDMMAAAA):", value="31072026")

if st.button("🚀 Extraer Datos", type="primary"):
    try:
        dt_inicio = datetime.strptime(fecha_input_inicio.strip(), "%d%m%Y")
        dt_fin = datetime.strptime(fecha_input_fin.strip(), "%d%m%Y")

        df_acumulado = pd.DataFrame()
        rango_meses = pd.date_range(start=dt_inicio.replace(day=1), end=dt_fin, freq='MS')

        with st.status("Procesando datos en Aduanet...", expanded=True) as status:
            for mes_dt in rango_meses:
                año_actual, mes_actual = mes_dt.year, mes_dt.month
                dia_inicio = dt_inicio.day if (año_actual == dt_inicio.year and mes_actual == dt_inicio.month) else 1
                dia_fin = dt_fin.day if (año_actual == dt_fin.year and mes_actual == dt_fin.month) else calendar.monthrange(año_actual, mes_actual)[1]

                f_inicio = f"{dia_inicio:02d}/{mes_actual:02d}/{año_actual}"
                f_fin = f"{dia_fin:02d}/{mes_actual:02d}/{año_actual}"

                st.write(f"📅 Consultando mes: {f_inicio} al {f_fin}")
                
                # Ya no necesitamos partir fechas ni hacer bucles de páginas
                df_mes, total_esperado = extraer_datos_aduanet_vista_unica(f_inicio, f_fin, ruc_input)
                
                estado = "✅" if len(df_mes) == total_esperado else "❗"
                st.write(f"&nbsp;&nbsp;&nbsp;{estado} Obtenidos {len(df_mes)} de {total_esperado} registros reportados por SUNAT")
                
                if not df_mes.empty: 
                    df_acumulado = pd.concat([df_acumulado, df_mes], ignore_index=True)
                    
            status.update(label="¡Extracción completada!", state="complete")

        if not df_acumulado.empty:
            st.success(f"✅ Se consolidaron {len(df_acumulado)} registros en total.")
            buffer = BytesIO()
            df_acumulado.to_excel(buffer, index=False, engine='openpyxl')
            st.download_button(label="📥 Descargar Excel", data=buffer.getvalue(), file_name=f"Exportaciones_{fecha_input_inicio}_al_{fecha_input_fin}.xlsx", mime="application/vnd.ms-excel")
        else:
            st.warning("No se encontraron datos.")
    except ValueError:
        st.error("Formato de fecha incorrecto. Usa DDMMAAAA.")
