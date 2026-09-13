import os
import pandas as pd
import math
import re
from io import StringIO
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import requests
from datetime import datetime
import calendar

def descargar_exportaciones_hibrido(fecha_inicio, fecha_fin, ruc, tamanio_pagina=20, pausa=1):
    opciones = Options()
    opciones.add_argument("--headless")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

    print(f"Abriendo conexión con Aduanet para el periodo {fecha_inicio}...")
    servicio = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=servicio, options=opciones)

    url_busqueda = f"http://www.aduanet.gob.pe/cl-ad-consdespade/ConsExportIAServlet?accion=infDeta&FecInicial={fecha_inicio}&FecFinal={fecha_fin}&codseleccion=exportador&dato={ruc}&flagBusq=1&pTipoConsulta=infDeta"
    driver.get(url_busqueda)
    time.sleep(3) 
    
    html_pagina1 = driver.page_source
    cookies_selenium = driver.get_cookies()
    driver.quit() 
    
    sesion = requests.Session()
    for cookie in cookies_selenium:
        sesion.cookies.set(cookie['name'], cookie['value'])
        
    sesion.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": url_busqueda
    })

    match_total = re.search(r"a\s+\d+\s+de\s+(\d+)", html_pagina1)
    if not match_total:
        print(" No se detectaron registros para este periodo.")
        return pd.DataFrame()
        
    total_registros = int(match_total.group(1))
    total_paginas = math.ceil(total_registros / tamanio_pagina)
    print(f"Registros encontrados: {total_registros} ({total_paginas} páginas)")

    tablas_pagina1 = pd.read_html(StringIO(html_pagina1))
    todas_las_tablas = [max(tablas_pagina1, key=len)]

    url_paginacion = "http://www.aduanet.gob.pe/cl-ad-consdespade/FrmPolizaporDetalle.jsp"
    
    for pagina in range(2, total_paginas + 1):
        payload_paginacion = {
            "tamanioPagina": str(tamanio_pagina),
            "pagina": str(pagina),
        }
        resp_pag = sesion.post(url_paginacion, data=payload_paginacion)
        resp_pag.encoding = "ISO-8859-1"
        tablas_pag = pd.read_html(StringIO(resp_pag.text))
        todas_las_tablas.append(max(tablas_pag, key=len))
        time.sleep(pausa)

    df_final = pd.concat(todas_las_tablas, ignore_index=True)
    
    if not df_final.empty:
        col_primera = df_final.columns[0]
        df_final[col_primera] = df_final[col_primera].astype(str)
        
        mascara_basura = df_final[col_primera].str.lower().str.contains(
            r'declaracion|declara|exportador|fec\.num|fob|neto', na=False
        )
        df_final = df_final[~mascara_basura]
        
        df_final = df_final.dropna(how='all')
        df_final.reset_index(drop=True, inplace=True)
        
        columnas_deseadas = [
            'DESCLARACION', 'EXPORTADOR', 'FEC.NUM', 'AGENTE', "CANT SERIE'S", 
            'FOB TOT.', 'ALMACEN', 'AFORO', 'NETO TOT', '# BULTOS', 'PAIS DEST', 
            'SERIE', 'PARTIDA', 'DESC. COMER', 'DESC. PREST', 'DESC. MAT. CONST', 
            'DES. USO', 'DESC. OTROS', 'CANT', 'UNID.', 'PESO NETO', 'FOB'
        ]
        
        if len(df_final.columns) == len(columnas_deseadas):
            df_final.columns = columnas_deseadas
        else:
            df_final = df_final.iloc[:, :len(columnas_deseadas)]
            df_final.columns = columnas_deseadas[:len(df_final.columns)]
        
    return df_final


# --- CONFIGURACIÓN DE PARÁMETROS INTERACTIVOS ---
print("\n" + "="*50)
print("   EXTRACTOR AUTOMÁTICO DE EXPORTACIONES SUNAT")
print("="*50 + "\n")

ruc = "20451899881" # Fijo por defecto

# Solicitar fechas al usuario
fecha_input_inicio = input("Ingrese la FECHA DE INICIO (Formato DDMMAAAA, ej. 01012026): ").strip()
fecha_input_fin = input("Ingrese la FECHA DE FIN (Formato DDMMAAAA, ej. 30092026): ").strip()

# Convertir los textos a objetos de fecha reales
try:
    dt_inicio = datetime.strptime(fecha_input_inicio, "%d%m%Y")
    dt_fin = datetime.strptime(fecha_input_fin, "%d%m%Y")
except ValueError:
    print("\n⚠️ ERROR: El formato de fecha es incorrecto. Debe ser DDMMAAAA (ej. 01012026).")
    print("El programa se cerrará. Vuelva a ejecutarlo.")
    time.sleep(5)
    exit()

ruta_carpeta = r"C:\Users\iarroyo\OneDrive - PROCESADORA LARAN SAC\PROLAN - Dpto. Costos\POWER BI - Costos\Impuestos\Exportaciones Sunat"
os.makedirs(ruta_carpeta, exist_ok=True)

marca_tiempo = datetime.now().strftime("%Y%m%d_%H%M%S")
print(f"\nINICIANDO EXTRACCIÓN Y LIMPIEZA - {datetime.now().strftime('%d/%m/%Y %H:%M')}")

# TABLA MAESTRA VACÍA
df_acumulado = pd.DataFrame()

# Generar la lista de meses que caen dentro del rango ingresado
rango_meses = pd.date_range(start=dt_inicio.replace(day=1), end=dt_fin, freq='MS')

for mes_dt in rango_meses:
    año_actual = mes_dt.year
    mes_actual = mes_dt.month
    
    # Determinar el día exacto de inicio y fin para el fragmento actual
    dia_inicio = dt_inicio.day if (año_actual == dt_inicio.year and mes_actual == dt_inicio.month) else 1
    ultimo_dia_mes = calendar.monthrange(año_actual, mes_actual)[1]
    dia_fin = dt_fin.day if (año_actual == dt_fin.year and mes_actual == dt_fin.month) else ultimo_dia_mes
    
    fecha_str_inicio = f"{dia_inicio:02d}/{mes_actual:02d}/{año_actual}"
    fecha_str_fin = f"{dia_fin:02d}/{mes_actual:02d}/{año_actual}"
    
    print(f"\n{'-'*50}")
    print(f" Procesando fragmento: {fecha_str_inicio} al {fecha_str_fin}")
    
    df = descargar_exportaciones_hibrido(fecha_str_inicio, fecha_str_fin, ruc)
    
    if not df.empty:
        # Pega los datos extraídos debajo de los datos anteriores en la tabla maestra
        df_acumulado = pd.concat([df_acumulado, df], ignore_index=True)
        print(f" Datos acumulados correctamente ({len(df)} filas).")
    else:
        print(f" FRAGMENTO OMITIDO: Sin datos.")
        
    time.sleep(3)

print(f"\n{'-'*50}")

# EXPORTACIÓN ÚNICA
if not df_acumulado.empty:
    nombre_archivo = f"exportaciones_{fecha_input_inicio}_al_{fecha_input_fin}_v{marca_tiempo}.xlsx"
    ruta_completa = os.path.join(ruta_carpeta, nombre_archivo)
    
    df_acumulado.to_excel(ruta_completa, index=False)
    print(f"✅ EXCEL CONSOLIDADO GUARDADO: {nombre_archivo} ({len(df_acumulado)} filas totales)")
else:
    print("⚠️ NO SE ENCONTRARON DATOS EN NINGÚN MES DEL RANGO ESPECIFICADO.")

print(" PROCESO DE LIMPIEZA FINALIZADO")