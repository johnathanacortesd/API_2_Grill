# Al inicio de pipeline.py, agrega la importación:
from ai_analyzer import enrich_rows_with_ai

# En la función generate_output_excel, permite columnas dinámicas:
def generate_output_excel(rows, km, progress: ProgressCb = None, columns_to_use: List[str] = None):
    cols = columns_to_use or OUTPUT_COLUMNS
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(
        buf,
        {
            "constant_memory": True,
            "strings_to_urls": False,
            "nan_inf_to_errors": False,
        },
    )
    ws = wb.add_worksheet("Resultado")
    fmt_header = wb.add_format({"bold": True})
    fmt_link = wb.add_format({"font_color": "#0563C1", "underline": 1, "align": "left"})
    fmt_date = wb.add_format({"num_format": "DD/MM/YYYY"})
    fmt_currency = wb.add_format({"num_format": "$#,##0"})
    fmt_thousands = wb.add_format({"num_format": "#,##0"})

    for i, col_name in enumerate(cols):
        if col_name in ["Título", "Resumen - Aclaracion", "resumen corto"]:
            ws.set_column(i, i, 50)
        elif col_name in ["Link Nota", "Link (Streaming - Imagen)"]:
            ws.set_column(i, i, 15)
        elif col_name in ["Subtema_IA", "Tema_IA"]:
            ws.set_column(i, i, 25)
        else:
            ws.set_column(i, i, 20)
        ws.write(0, i, col_name, fmt_header)

    n = len(rows)
    step = max(1, n // 50) if n else 1
    emit_progress(progress, 0, f"Generando archivo de resultado… 0/{n} filas")

    try:
        # Pasa cols a _write_xlsx_rows
        _write_xlsx_rows(ws, rows, km, n, step, progress, fmt_link, fmt_date, fmt_currency, fmt_thousands, cols)
        emit_progress(progress, 100, "Guardando archivo Excel…")
    finally:
        wb.close()
    return buf.getvalue()


def _write_xlsx_rows(ws, rows, km, n, step, progress, fmt_link, fmt_date, fmt_currency, fmt_thousands, cols):
    for i, row in enumerate(rows):
        tk = km.get("titulo")
        if tk and tk in row:
            row[tk] = clean_title_for_output(row.get(tk))
        rk = km.get("resumen")
        if rk and rk in row:
            row[rk] = corregir_texto(row.get(rk))

        excel_row = i + 1
        for cidx, h in enumerate(cols):
            val = row.get(h)
            cv = None
            url = None

            if h == "Fecha" and val is not None and not isinstance(val, dict) and pd.notna(val):
                if isinstance(val, pd.Timestamp):
                    cv = val.to_pydatetime()
                elif isinstance(val, (datetime.datetime, datetime.date)):
                    cv = val
                else:
                    cv = str(val)
            elif h in NUMERIC_COLS:
                cv = parse_numeric(val)
            elif isinstance(val, dict) and "url" in val:
                cv = val.get("value", "Link")
                if val.get("url"):
                    url = val["url"]
            elif val is not None:
                if isinstance(val, str) and val.startswith("http"):
                    cv = "Link"
                    url = val
                else:
                    cv = str(val)

            if url:
                display = str(cv or "Link")
                try:
                    ws.write_url(excel_row, cidx, str(url), fmt_link, string=display)
                except Exception:
                    ws.write(excel_row, cidx, display, fmt_link)
            elif h == "Fecha" and isinstance(cv, datetime.datetime):
                ws.write_datetime(excel_row, cidx, cv, fmt_date)
            elif h == "Fecha" and isinstance(cv, datetime.date):
                ws.write_datetime(
                    excel_row,
                    cidx,
                    datetime.datetime(cv.year, cv.month, cv.day),
                    fmt_date,
                )
            elif h in CURRENCY_COLS and isinstance(cv, (int, float)) and math.isfinite(cv):
                ws.write_number(excel_row, cidx, cv, fmt_currency)
            elif h in THOUSANDS_COLS and isinstance(cv, (int, float)) and math.isfinite(cv):
                ws.write_number(excel_row, cidx, cv, fmt_thousands)
            elif cv is None or cv == "":
                ws.write_blank(excel_row, cidx, None)
            elif isinstance(cv, float) and not math.isfinite(cv):
                ws.write_blank(excel_row, cidx, None)
            else:
                ws.write(excel_row, cidx, cv)

        if progress and (i % step == 0 or i == n - 1):
            emit_progress(
                progress,
                int((i + 1) / n * 100) if n else 100,
                f"Generando archivo de resultado… {i + 1}/{n} filas",
            )


# Modifica la firma de process_dossier para aceptar ai_config:
def process_dossier(
    file_obj,
    region_map,
    internet_map,
    progress: ProgressCb = None,
    ai_config: Optional[dict] = None
) -> dict:
    t0 = time.time()
    emit_progress(progress, 2, "Cargando archivo…")
    file_bytes = file_to_bytes(file_obj)

    df_normalized = load_dossier_dataframe(file_bytes, progress=progress)
    del file_bytes
    df_normalized = normalize_dossier_dataframe(df_normalized, region_map, internet_map, progress=progress)

    medios_sin_region = []
    if not df_normalized.empty and "Región" in df_normalized.columns and "Medio" in df_normalized.columns:
        medios_sin_region = sorted(set(
            df_normalized.loc[df_normalized["Región"] == "N/A", "Medio"]
            .astype(str).str.strip()
        ) - {"", "nan", "None"})

    emit_progress(progress, 55, "Expandiendo menciones…")
    rows_expanded = expand_menciones(df_normalized)
    del df_normalized
    gc.collect()

    emit_progress(progress, 62, "Detectando duplicados…")
    rows = detectar_duplicados_avanzado(rows_expanded, KEY_MAP)
    for row in rows:
        if row["is_duplicate"]:
            row["Tono"] = "Duplicada"
            row["Tema"] = "-"
            row["Subtema"] = "-"

    # ENRIQUECIMIENTO CON IA (Si está habilitado en ai_config)
    cols_to_export = list(OUTPUT_COLUMNS)
    if ai_config and ai_config.get("enabled"):
        emit_progress(progress, 70, "Iniciando análisis reputacional de IA…")
        rows = enrich_rows_with_ai(
            rows=rows,
            km=KEY_MAP,
            brand=ai_config["brand"],
            aliases=ai_config.get("aliases", []),
            api_key=ai_config["api_key"],
            model=ai_config.get("model", "gpt-4.1-nano-2025-04-14"),
            progress_callback=progress
        )
        cols_to_export.extend(["Tono_IA", "Tema_IA", "Subtema_IA"])

    emit_progress(progress, 94, "✓ Estructuración finalizada. Generando archivo Excel…")

    unique_rows = sum(1 for r in rows if not r.get("is_duplicate"))
    total_rows = len(rows)

    def export_progress(pct, msg):
        overall = 94 + int(pct * 0.06)
        emit_progress(progress, overall, msg)

    output_data = generate_output_excel(rows, KEY_MAP, progress=export_progress, columns_to_use=cols_to_export)
    del rows, rows_expanded
    gc.collect()
    duration = time.time() - t0
    emit_progress(progress, 100, "Limpieza y análisis completados")

    return {
        "output_data": output_data,
        "output_filename": f"Dossier_Estructurado_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        "total_rows": total_rows,
        "unique_rows": unique_rows,
        "duplicates": total_rows - unique_rows,
        "process_duration": f"{duration:.2f}s",
        "medios_sin_mapear": medios_sin_region,
    }
