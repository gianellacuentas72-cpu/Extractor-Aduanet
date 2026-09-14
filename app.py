import streamlit as st
import pandas as pd
import math
import re
from io import BytesIO, StringIO
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import requests
from datetime import datetime
import calendar

st.set_page_config(page_title="Extractor Sunat - Prolan", page_icon="📊", layout="centered")
st.title("📊 Extractor Automático de Exportaciones")

# --- EL ALGORITMO INTELIGENTE PARA TABLAS ANIDADAS ---
def extraer_tabla_correcta(html_source):
    try:
        tablas = pd.read_html(StringIO(html_source))
        candidatas = []
        for t in tablas:
            if t.shape[1] >= 15:
                # Estandarizamos los nombres de columnas a números (0, 1, 2...)
                t.columns = range(t.shape[1])
                # Contamos cuántas filas válidas (DUA) tiene esta tabla
                mask = t[0].astype(str).str.contains(r'\d{3}-\d{4}-\d+', regex=True) | \
                       t[1].astype(str).str.contains(r'\d{3}-\d{4}-\d+', regex=True)
                filas_validas = mask.sum()
                
                if filas_validas > 0:
                    # Guardamos: (Cantidad_DUAS, Total_Filas, DataFrame)
                    candidatas.append((filas_validas, t.shape[0], t))
        
        if candidatas:
            # MAGIA: Ordenamos priorizando MÁS filas válidas, y en caso de empate, MENOS filas totales (para descartar la tabla externa)
            candidatas.sort(key=lambda x: (x[0], -x[1]), reverse=True)
            return candidatas[0][2] # Retornamos solo la tabla perfecta
    except Exception:
        pass
    return pd.DataFrame()


@st.cache_data(show_spinner=False)
def descargar_exportaciones_hibrido(fecha_inicio, fecha_fin, ruc):
    opciones = Options()
    opciones.add_argument("--headless")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu") 
    opciones.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

    opciones.binary_location = "/usr/bin/chromium"
    servicio = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=servicio, options=opciones)

    # 1. Obtenemos la Página 1
    url_busqueda = f"http://www.aduanet.gob.pe/cl-ad-consdespade/ConsExportIAServlet?accion=infDeta&FecInicial={fecha_inicio}&FecFinal={fecha_fin}&codseleccion=exportador&dato={ruc}&flagBusq=1&pTipoConsulta=infDeta"
    driver.get(url_busqueda)
    time.sleep(3) 
    
    html_pagina1 = driver.page_source
    cookies_selenium = driver.get_cookies()
    driver.quit() 
    
    sesion = requests.Session()
    for cookie in cookies_selenium:
        sesion.cookies.set(cookie['name'], cookie['value'])
        
    sesion.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": url_busqueda})

    match_total = re.search(r"a\s+\d+\s+de\s+(\d+)", html_pagina1)
    if not match_total: return pd.DataFrame()
        
    total_registros = int(match_total.group(1))
    total_paginas = math.ceil(total_registros / 20)

    todas_las_tablas = []
    
    # Extraemos Página 1
    t1 = extraer_tabla_correcta(html_pagina1)
    if not t1.empty:
        todas_las_tablas.append(t1)

    # Extraemos Páginas 2 en adelante
    url_paginacion = "http://www.aduanet.gob.pe/cl-ad-consdespade/FrmPolizaporDetalle.jsp"
    for pagina in range(2, total_paginas + 1):
        resp_pag = sesion.post(url_paginacion, data={"tamanioPagina": "20", "pagina": str(pagina)})
        resp_pag.encoding = "ISO-8859-1"
        t_pag = extraer_tabla_correcta(resp_pag.text)
        if not t_pag.empty:
            todas_las_tablas.append(t_pag)
        time.sleep(1)

    if not todas_las_tablas: return pd.DataFrame()

    df_final = pd.concat(todas_las_tablas, ignore_index=True)
    
    # --- LIMPIEZA FINAL ---
    col0 = df_final.columns[0]
    col1 = df_final.columns[1] if len(df_final.columns) > 1 else col0
    
    # Nos quedamos estrictamente con las filas de datos
    mask = df_final[col0].astype(str).str.contains(r'\d{3}-\d{4}-\d+', regex=True) | \
           df_final[col1].astype(str).str.contains(r'\d{3}-\d{4}-\d+', regex=True)
           
    df_final = df_final[mask].reset_index(drop=True)
    
    # Formateamos las 22 columnas exactas
    cols = ['DESCLARACION', 'EXPORTADOR', 'FEC.NUM', 'AGENTE', "CANT SERIE'S", 'FOB TOT.', 'ALMACEN', 'AFORO', 'NETO TOT', '# BULTOS', 'PAIS DEST', 'SERIE', 'PARTIDA', 'DESC. COMER', 'DESC. PREST', 'DESC. MAT. CONST', 'DES. USO', 'DESC. OTROS', 'CANT', 'UNID.', 'PESO NETO', 'FOB']
    df_final = df_final.iloc[:, :len(cols)]
    df_final.columns = cols[:len(df_final.columns)]
    
    # Arreglamos los números
    if 'FOB' in df_final.columns:
        df_final['FOB'] = df_final['FOB'].astype(str).str.replace(',', '', regex=False).str.strip()
        df_final['FOB'] = pd.to_numeric(df_final['FOB'], errors='coerce')
        
    return df_final


ruc_input = st.text_input("RUC de la empresa:", value="20451899881")
col1, col2 = st.columns(2)
with col1: fecha_input_inicio = st.text_input("Fecha Inicio (DDMMAAAA):", value="01012026")
with col2: fecha_input_fin = st.text_input("Fecha Fin (DDMMAAAA):", value="31012026")

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
                
                st.write(f"Consultando: {f_inicio} al {f_fin}")
                df_mes = descargar_exportaciones_hibrido(f_inicio, f_fin, ruc_input)
                if not df_mes.empty: df_acumulado = pd.concat([df_acumulado, df_mes], ignore_index=True)
            status.update(label="¡Extracción completada!", state="complete")

        if not df_acumulado.empty:
            st.success(f"✅ Se consolidaron {len(df_acumulado)} registros.")
            buffer = BytesIO()
            df_acumulado.to_excel(buffer, index=False, engine='openpyxl')
            st.download_button(label="📥 Descargar Excel", data=buffer.getvalue(), file_name=f"Exportaciones_{fecha_input_inicio}_al_{fecha_input_fin}.xlsx", mime="application/vnd.ms-excel")
        else:
            st.warning("No se encontraron datos.")
    except ValueError:
        st.error("Formato de fecha incorrecto. Usa DDMMAAAA.")
