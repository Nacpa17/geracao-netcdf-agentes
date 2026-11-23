"""Como o esquema do RD-80 em geracao_netcdf/aparelhos/rd80/ foi derivado do JSON do
grupo (RD80-Processing 3.1, input/support/). Fica aqui para registro e para refazer
se o JSON do grupo mudar:

    python scripts/limpar_esquema_rd80.py RD80-Processing/input/support/netCDF_info.json \\
        RD80-Processing/input/support/variables_info.json \\
        geracao_netcdf/aparelhos/rd80/netCDF_info.json geracao_netcdf/aparelhos/rd80/variables_info.json

O que muda em relação ao JSON do grupo:

1. Variáveis: as 19 do catálogo do ATTO + QC + time_bounds (26 no total). A trilha
   de auditoria (observation_*, time_observed, integration_source_*,
   source_record_count) e os qc_*_summary ficam de fora; a variável "bound" também
   (a dimensão de limites não precisa de coordenada: ARM 1.3, 6.1.4 e 6.2).
2. Metadados no padrão ARM Data File Standards 1.3 (DOE/SC-ARM-15-004):
   - time: standard_name "time" e bounds "time_bounds" (6.1.3-6.1.4); time_bounds
     sem units e sem missing_value, em segundos relativos a time:units (6.1.4) —
     no 3.1 eles estão em segundos desde 1970 sem units, o que um leitor CF
     interpreta como segundos desde a meia-noite;
   - base_time <-> time_offset ligados por ancillary_variables (6.1.2);
   - tempo e coordenadas sem missing_value (6.1.1, 6.2);
   - lat/lon/alt com standard_name (6.3);
   - unidade adimensional "1" em vez de "unitless" (3.1, 6.6.1);
   - QC com bits: long_name "Quality check results on variable: ...", units "1",
     description padrão, flag_method "bit", standard_name "quality_flag" (6.8.2);
     o texto explicativo do grupo vai para "comment"; bits declarados que o núcleo
     não usa (agregação) viram "Not used" (6.8.5), mantendo a numeração do 3.1;
   - qc_calibration é indicador de estado inclusivo (flag_masks/flag_meanings, 6.5.2),
     não teste de qualidade.
3. ancillary_variables só com variáveis que existem (e sem coordenadas);
   "conventions" -> "Conventions" e "Description" -> "description"; o global
   location_description (obrigatório na ARM, 6.7.1) repete o "location" do grupo;
   globais da execução como __AUTO__; unidade de num_drop_density igual à de
   distribution_intercept.

Valores escolhidos pelo grupo (Conventions, data_level, doi, datastream) ficam
como estão; o validador com --padrao arm aponta o que falta para o padrão ARM.
"""
import json
import sys

src, src_const, dst, dst_const = sys.argv[1:5]
d = json.load(open(src, encoding="utf-8"))
NUCLEO = ["base_time", "time_offset", "time", "time_bounds", "lat", "lon", "alt",
          "drop_avg_class", "fall_velocity", "delta_diam", "num_drop",
          "number_detected_drops", "qc_number_detected_particles", "diam_max", "diam_min",
          "num_drop_density", "rain_rate", "radar_reflectivity", "liq_water", "energy_flux",
          "slope_parameter", "distribution_intercept", "qc_time", "sample_interval",
          "qc_sample_interval", "qc_calibration"]
COORDENADAS = {"time", "drop_avg_class"}
QC_BITS = ("qc_time", "qc_number_detected_particles", "qc_sample_interval")
ARM_QC_DESCRICAO = ("This variable contains bit packed integer values, where each bit "
                    "represents a QC test on the data. Non-zero bits indicate the QC "
                    "condition given in the description for those bits; a value of 0 "
                    "(no bits set) indicates the data has not failed any QC tests.")

variaveis = {}
for nome in NUCLEO:
    b = dict(d["variables"][nome])
    if isinstance(b.get("ancillary_variables"), str):
        b["ancillary_variables"] = " ".join(
            x for x in b["ancillary_variables"].split()
            if x in NUCLEO and x not in COORDENADAS and x != nome) or "__AUTO__"
    if b.get("units") == "unitless":
        b["units"] = "1"
    variaveis[nome] = b

# tempo (ARM 1.3, 6.1)
for nome in ("base_time", "time_offset", "time", "time_bounds", "drop_avg_class"):
    for k in ("missing_value", "valid_min", "valid_max"):
        variaveis[nome].pop(k, None)
variaveis["base_time"]["ancillary_variables"] = "time_offset"
variaveis["time_offset"]["ancillary_variables"] = "base_time"
variaveis["time"].update({
    "standard_name": "time", "bounds": "time_bounds",
    "comment": ("Start of each observation rounded to the nearest nominal time step "
                "(exact half step goes forward); no resampling. The actual integration "
                "window is in time_bounds.")})
tb = variaveis["time_bounds"]
tb.pop("units", None)
tb.update({
    "long_name": "Observed start and end of each integration",
    "comment": ("Seconds relative to time:units (ARM Data File Standards 1.3, 6.1.4). Start "
                "is the original start of the selected record (DISDRODATA marks the "
                "beginning of the interval) and end is start + sample_interval. Time steps "
                "without observation carry the nominal step [time, time + step]; see "
                "qc_time and qc_sample_interval.")})
for nome, padrao in (("lat", "latitude"), ("lon", "longitude"), ("alt", "altitude")):
    variaveis[nome]["standard_name"] = padrao

variaveis["sample_interval"]["comment"] = (
    "Integration interval reported by the instrument; only positive values are used in "
    "the calculations.")
variaveis["num_drop_density"]["units"] = "1/(m^3 mm)"   # igual ao distribution_intercept

extras = {k: dict(v) for k, v in d["variable_specific_attrs"].items()
          if k in NUCLEO and k != "qc_time"}

# QC com bits (ARM 1.3, 6.8.2-6.8.5)
for nome in QC_BITS:
    b = variaveis[nome]
    alvo = {"qc_time": "time", "qc_sample_interval": "sample_interval",
            "qc_number_detected_particles": "number_detected_drops"}[nome]
    b["long_name"] = "Quality check results on variable: " + variaveis[alvo]["long_name"]
    b.update({"units": "1", "flag_method": "bit", "standard_name": "quality_flag"})
    if b.get("valid_max") in ("N/A", "__AUTO__"):
        b.pop("valid_max")
    e = extras.setdefault(nome, {})
    if e.get("description"):
        e["comment"] = e["description"]
    e["description"] = ARM_QC_DESCRICAO
extras["qc_number_detected_particles"]["comment"] = (
    "A dry valid record sets bit 1 as information, not a failed measurement. Time steps "
    "without observation have undefined counts and interval (bits 3 and 4); see qc_time.")
qsi = extras["qc_sample_interval"]
for bit in (4, 5):
    qsi[f"bit_{bit}_comment"] = ("Kept so that bit numbering matches RD80-Processing 3.1, "
                                 "which aggregates records; this generator never does.")
    qsi[f"bit_{bit}_description"] = "Not used"
    qsi[f"bit_{bit}_assessment"] = "Bad"
qsi["flag_meanings"] = ("invalid_interval below_nominal above_nominal not_used_4 "
                        "not_used_5 no_observation")
qsi["comment"] = ("No tolerance: any positive interval below or above the nominal step is "
                  "flagged.")

# qc_calibration: estado inclusivo (ARM 1.3, 6.5.2)
qc_cal = variaveis["qc_calibration"]
qc_cal.update({"long_name": "Calibration period indicator (bit-packed)", "units": "1"})
qc_cal.pop("valid_max", None)
extras["qc_calibration"] = {
    "flag_masks": [1, 2, 4],
    "flag_meanings": ("on_or_after_calibration_1 on_or_after_calibration_2 "
                      "on_or_after_calibration_3"),
    "bit_1_description": "Data on or after first calibration date (see calib_date).",
    "bit_2_description": "Data on or after second calibration date.",
    "bit_3_description": "Data on or after third calibration date.",
    "comment": ("State indicator, not a quality test. Zero means data before all "
                "calibration dates or a time step without observation."),
}

g = {}
for nome, b in d["global attributes"].items():
    b = dict(b)
    if nome == "conventions":
        nome = "Conventions"
        b["Name"] = "Conventions"
    if nome == "Description":              # nomes de atributo em minúsculas (ARM 6.7.1)
        nome = "description"
        b["Name"] = "description"
    if nome in ("command_line", "process_version", "history", "input_source", "calib_date"):
        b["value"] = "__AUTO__"            # preenchidos pela execução real
    if nome == "sampling_interval":
        b["value"] = "__AUTO__"            # "60 seconds" (vem do integration_time)
    if nome == "effective_measurement_area":
        b["value"] = "__AUTO__"            # "0.005 m^2" (vem do sensor_area)
    if nome == "averaging_interval":
        b["value"] = "None"
    g[nome] = b
    if nome == "location" and "location_description" not in d["global attributes"]:
        # a ARM exige location_description (6.7.1); o grupo usa "location"
        g["location_description"] = dict(b, Name="location_description")
out = {"_sobre": ("Esquema do RD-80 (ATTO) para geracao_netcdf, derivado de "
                  "input/support/netCDF_info.json do RD80-Processing 3.1 por "
                  "scripts/limpar_esquema_rd80.py (ver lá o que muda e por quê). 19 variáveis "
                  "do catálogo do ATTO + QC + time_bounds, metadados no padrão ARM Data File "
                  "Standards 1.3. Campos de catálogo (Name, Variable ID, ...) não vão para o "
                  "NetCDF."),
       "global attributes": g,
       "dimensions": {"time": d["dimensions"]["time"],
                      "drop_avg_class": d["dimensions"]["drop_avg_class"],
                      "bound": "2: start and end of each integration (time_bounds)"},
       "variables": variaveis, "variable_specific_attrs": extras}
json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=4)
c = json.load(open(src_const, encoding="utf-8"))
c.pop("nominal_time_step", None)
c.pop("rounding_rule", None)
json.dump(c, open(dst_const, "w", encoding="utf-8"), ensure_ascii=False, indent=4)
print(len(variaveis), "variáveis;", len(g), "globais")
