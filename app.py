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

UMBRAL_REGISTROS = 150  # por encima de esto, se parte el rango de fechas en dos y se reintenta cada mitad


def _contar_duas(t):
    if t is None:
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
def _descargar_bloque(fecha_inicio, fecha_fin, ruc):
    """Descarga un rango de fechas asumiendo que cabe dentro de UNA sola sesión (pocos registros)."""
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
    driver.get(url_busqueda)
    time.sleep(3)

    html_inicial = driver.page_source
    cookies_selenium = driver.get_cookies()
    driver.quit()

    match_total = re.search(r"a\s+\d+\s+de\s+(\d+)", html_inicial)
    if not match_total:
        return pd.DataFrame(), 0
    total_registros = int(match_total.group(1))

    tabla_inicial = _extraer_mejor_tabla(html_inicial)
    todas_las_tablas = []

    if tabla_inicial is not None and _contar_duas(tabla_inicial) >= total_registros * 0.9:
        todas_las_tablas.append(tabla_inicial)
    else:
        sesion = requests.Session()
        for cookie in cookies_selenium:
            sesion.cookies.set(cookie['name'], cookie['value'])
        sesion.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": url_busqueda})

        total_paginas = math.ceil(total_registros / 20)
        url_paginacion = "http://www.aduanet.gob.pe/cl-ad-consdespade/FrmPolizaporDetalle.jsp"

        for pagina in range(1, total_paginas + 1):
            esperadas = 20 if pagina < total_paginas else total_registros - 20 * (total_paginas - 1)
            mejor_intento, mejor_duas = None, -1

            for intento in range(1, 4):
                resp_pag = sesion.post(url_paginacion, data={"tamanioPagina": "20", "pagina": str(pagina)})
                resp_pag.encoding = "ISO-8859-1"
                t = _extraer_mejor_tabla(resp_pag.text)
                duas_obtenidas = _contar_duas(t)
                if duas_obtenidas > mejor_duas:
                    mejor_duas, mejor_intento = duas_obtenidas, t
                if duas_obtenidas >= esperadas:
                    break
                time.sleep(2 * intento)

            if mejor_intento is not None:
                todas_las_tablas.append(mejor_intento)
            time.sleep(1.5)

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


def descargar_exportaciones_periodo(fecha_inicio, fecha_fin, ruc, umbral=UMBRAL_REGISTROS, profundidad=0):
    """
    Descarga un rango de fechas dado, partiéndolo automáticamente en dos mitades
    (recursivo) cuando tiene demasiados registros para caber en una sola sesión.
    fecha_inicio / fecha_fin: strings "dd/mm/aaaa"
    """
    sangria = "  " * profundidad
    df_bloque, total = _descargar_bloque(fecha_inicio, fecha_fin, ruc)

    dt_ini = datetime.strptime(fecha_inicio, "%d/%m/%Y")
    dt_fin = datetime.strptime(fecha_fin, "%d/%m/%Y")

    if total > umbral and dt_fin > dt_ini:
        # Demasiados registros para una sola sesión: partir el rango en dos mitades
        dias_totales = (dt_fin - dt_ini).days
        dt_medio = dt_ini + timedelta(days=dias_totales // 2)
        f_medio_fin = dt_medio.strftime("%d/%m/%Y")
        f_medio_ini = (dt_medio + timedelta(days=1)).strftime("%d/%m/%Y")

        st.write(f"{sangria}⚠️ {fecha_inicio}-{fecha_fin} tiene {total} registros (> {umbral}), dividiendo en dos...")
        df1 = descargar_exportaciones_periodo(fecha_inicio, f_medio_fin, ruc, umbral, profundidad + 1)
        df2 = descargar_exportaciones_periodo(f_medio_ini, fecha_fin, ruc, umbral, profundidad + 1)
        return pd.concat([df1, df2], ignore_index=True)
    else:
        st.write(f"{sangria}✅ {fecha_inicio}-{fecha_fin}: esperados {total}, obtenidos {len(df_bloque)}")
        return df_bloque


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
                df_mes = descargar_exportaciones_periodo(f_inicio, f_fin, ruc_input)
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
