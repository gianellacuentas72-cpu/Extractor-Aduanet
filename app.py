import streamlit as st
import pandas as pd
import math
import re
from io import BytesIO, StringIO
import time
from datetime import datetime, timedelta
import calendar
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import requests

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
def descargar_bloque_fechas(fecha_inicio, fecha_fin, ruc):
    """Descarga un bloque pequeño de fechas sin saturar la memoria de Aduanet"""
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
        time.sleep(3)
        html_inicial = driver.page_source
        cookies_selenium = driver.get_cookies()
    finally:
        driver.quit() 

    match_total = re.search(r"a\s+\d+\s+de\s+(\d+)", html_inicial)
    if not match_total:
        return pd.DataFrame(), 0
        
    total_registros = int(match_total.group(1))
    total_paginas = math.ceil(total_registros / 20)

    todas_las_tablas = []
    
    # Rescatamos la Página 1
    tabla_inicial = _extraer_mejor_tabla(html_inicial)
    if tabla_inicial is not None and _contar_duas(tabla_inicial) > 0:
        todas_las_tablas.append(tabla_inicial)

    sesion = requests.Session()
    for cookie in cookies_selenium:
        sesion.cookies.set(cookie['name'], cookie['value'])
    sesion.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": url_busqueda})

    url_paginacion = "http://www.aduanet.gob.pe/cl-ad-consdespade/FrmPolizaporDetalle.jsp"

    for pagina in range(1, total_paginas + 1):
        esperadas = 20 if pagina < total_paginas else total_registros - 20 * (total_paginas - 1)
        mejor_tabla = None
        mejor_duas = -1

        for intento in range(1, 4):
            try:
                resp_pag = sesion.post(url_paginacion, data={"tamanioPagina": "20", "pagina": str(pagina)}, timeout=15)
                resp_pag.encoding = "ISO-8859-1"
                t = _extraer_mejor_tabla(resp_pag.text)
                duas = _contar_duas(t)
                
                if duas > mejor_duas:
                    mejor_duas = duas
                    mejor_tabla = t
                    
                if duas >= esperadas:
                    break 
            except Exception:
                pass
            time.sleep(1)

        if mejor_tabla is not None:
            todas_las_tablas.append(mejor_tabla)
            
        time.sleep(0.5)

    if not todas_las_tablas:
        return pd.DataFrame(), total_registros

    df_final = pd.concat(todas_las_tablas, ignore_index=True)
    df_final[0] = df_final[0].replace([None, 'nan', 'NaN', ''], pd.NA).ffill()
    df_final[1] = df_final[1].replace([None, 'nan', 'NaN', ''], pd.NA).ffill()

    if df_final.shape[1] >= 12:
        mask_serie = pd.to_numeric(df_final[11], errors='coerce').notna()
        df_final = df_final[mask_serie].reset_index(drop=True)

    cols = ['DESCLARACION', 'EXPORTADOR', 'FEC.NUM', 'AGENTE', "CANT SERIE'S", 'FOB TOT.', 'ALMACEN', 'AFORO', 'NETO TOT', '# BULTOS', 'PAIS DEST', 'SERIE', 'PARTIDA', 'DESC. COMER', 'DESC. PREST', 'DESC. MAT. CONST', 'DES. USO', 'DESC. OTROS', 'CANT', 'UNID.', 'PESO NETO', 'FOB']
    df_final = df_final.iloc[:, :22]
    df_final.columns = cols[:len(df_final.columns)]

    if 'FOB' in df_final.columns:
        df_final['FOB'] = df_final['FOB'].astype(str).str.replace(',', '', regex=False).str.strip()
        df_final['FOB'] = pd.to_numeric(df_final['FOB'], errors='coerce')

    df_final = df_final.drop_duplicates(ignore_index=True)
    return df_final, total_registros

ruc_input = st.text_input("RUC de la empresa:", value="20451899881")
col1, col2 = st.columns(2)
with col1: fecha_input_inicio = st.text_input("Fecha Inicio (DDMMAAAA):", value="01012026")
with col2: fecha_input_fin = st.text_input("Fecha Fin (DDMMAAAA):", value="31072026")

if st.button("🚀 Extraer Datos", type="primary"):
    try:
        dt_inicio = datetime.strptime(fecha_input_inicio.strip(), "%d%m%Y")
        dt_fin = datetime.strptime(fecha_input_fin.strip(), "%d%m%Y")

        df_acumulado = pd.DataFrame()
        
        # --- GENERADOR DE MICRO-BATCHES (10 DÍAS) ---
        intervalos = []
        fecha_actual = dt_inicio
        while fecha_actual <= dt_fin:
            fecha_siguiente = fecha_actual + timedelta(days=9)
            if fecha_siguiente > dt_fin:
                fecha_siguiente = dt_fin
            intervalos.append((fecha_actual.strftime("%d/%m/%Y"), fecha_siguiente.strftime("%d/%m/%Y")))
            fecha_actual = fecha_siguiente + timedelta(days=1)

        with st.status("Procesando datos en Aduanet...", expanded=True) as status:
            progreso_text = st.empty()
            
            for idx, (f_ini, f_fin) in enumerate(intervalos):
                progreso_text.write(f"📅 Extrayendo bloque {idx + 1} de {len(intervalos)}: {f_ini} al {f_fin}")
                
                df_bloque, total_esperado = descargar_bloque_fechas(f_ini, f_fin, ruc_input)
                
                estado = "✅" if len(df_bloque) >= (total_esperado * 0.98) else "❗"
                st.write(f"&nbsp;&nbsp;&nbsp;{estado} Bloque {f_ini}-{f_fin}: Obtenidos {len(df_bloque)} de {total_esperado}")
                
                if not df_bloque.empty: 
                    df_acumulado = pd.concat([df_acumulado, df_bloque], ignore_index=True)
                    
            progreso_text.empty()
            status.update(label="¡Extracción completada!", state="complete")

        if not df_acumulado.empty:
            st.success(f"✅ Se consolidaron {len(df_acumulado)} registros en total.")
            buffer = BytesIO()
            df_acumulado.to_excel(buffer, index=False, engine='openpyxl')
            st.download_button(label="📥 Descargar Excel", data=buffer.getvalue(), file_name=f"Exportaciones_{fecha_input_inicio}_al_{fecha_input_fin}.xlsx", mime="application/vnd.ms-excel")
        else:
            st.warning("No se encontraron datos en el rango seleccionado.")
    except ValueError:
        st.error("Formato de fecha incorrecto. Usa DDMMAAAA.")
