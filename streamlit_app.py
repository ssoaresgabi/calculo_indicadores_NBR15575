"""
Execução:  streamlit run streamlit_app.py
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd
import streamlit as st

HORAS_ANO = 8760
JOULE_POR_KWH = 3_600_000

# ---------------------------------------------------------------------------
# Ocupação
# ---------------------------------------------------------------------------

OCUPACAO = {
    # Sala/estar: 14h às 22h  → 2.920 h/ano
    "sala": [0] * 14 + [1] * 8 + [0] * 2,
    # Dormitório: 00h às 08h e 22h às 24h → 3.650 h/ano
    "dormitorio": [1] * 8 + [0] * 14 + [1] * 2,
    # Misto (sala + dormitório conjugados): 00h-08h e 14h-24h → 6.570 h/ano
    "misto": [1] * 8 + [0] * 6 + [1] * 10,
}


def perfil_ocupacao(tipo: str, n_horas: int = HORAS_ANO) -> pd.Series:
    """Expande o padrão diário para o ano inteiro."""
    base = OCUPACAO[tipo]
    dias = -(-n_horas // 24)
    return pd.Series((base * dias)[:n_horas], dtype=float)


# ---------------------------------------------------------------------------
# Faixas de temperatura operativa por intervalo
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Intervalo:
    numero: int
    descricao: str
    to_min: float | None      # limite inferior da faixa (None = não avaliado)
    to_max: float             # limite superior da faixa, para o PHFT
    limite_cgtr: float        # To a partir do qual a carga de refrigeração conta
    limite_cgta: float | None # To até o qual a carga de aquecimento conta
    avalia_aquecimento: bool


INTERVALOS = {
    1: Intervalo(1, "TBSm < 25 °C", 18.0, 26.0, 26.0, 18.0, True),
    2: Intervalo(2, "25 °C ≤ TBSm < 27 °C", None, 28.0, 28.0, None, False),
    # ATENÇÃO: a planilha oficial usa To < 28 para o PHFT do intervalo 3 e
    # To >= 30 para a carga de refrigeração. Ver nota em LEIAME.md.
    3: Intervalo(3, "TBSm ≥ 27 °C", None, 28.0, 30.0, None, False),
}


# ---------------------------------------------------------------------------
# Zoneamento bioclimático
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ZonaBioclimatica:
    codigo: str
    descricao: str
    intervalo: int
    avalia_tomin: bool


ZONAS_BIOCLIMATICAS = {
    "1R": ZonaBioclimatica("1R", "Muito fria com inverno rigoroso", 1, True),
    "1M": ZonaBioclimatica("1M", "Muito fria com inverno moderado", 1, True),
    "2R": ZonaBioclimatica("2R", "Fria com inverno rigoroso", 1, True),
    "2M": ZonaBioclimatica("2M", "Fria com inverno moderado", 1, True),
    "3A": ZonaBioclimatica("3A", "Mista e úmida", 1, True),
    "3B": ZonaBioclimatica("3B", "Mista e seca", 1, False),
    "4A": ZonaBioclimatica("4A", "Levemente quente e úmida", 1, True),
    "4B": ZonaBioclimatica("4B", "Levemente quente e seca", 1, True),
    "5A": ZonaBioclimatica("5A", "Quente e úmida", 2, False),
    "5B": ZonaBioclimatica("5B", "Quente e seca", 2, False),
    "6A": ZonaBioclimatica("6A", "Muito quente e úmida", 3, False),
    "6B": ZonaBioclimatica("6B", "Muito quente e seca", 3, False),
}


def classificar_zona_bioclimatica(
    tbsm: float, ur: float,
    latitude: float | None = None, longitude: float | None = None,
) -> str:
    """
    Classifica a zona bioclimática (NBR 15220-3:2024, 5.2) a partir da TBSm
    (temperatura média anual de bulbo seco externa, °C) e da UR (umidade
    relativa média anual externa, %). Latitude/longitude só decidem 1R×1M e
    2R×2M - fora dessas duas faixas de TBSm são ignoradas.
    """
    if tbsm < 18.8:
        if latitude is not None:
            if -30.0 <= latitude <= -27.2 and tbsm < 17.0:
                return "1R"
            if latitude < -30.0 and tbsm < 18.5:
                return "1R"
        return "1M"
    if tbsm < 20.9:
        if latitude is not None and longitude is not None \
                and latitude < -24.2 and longitude < -50.3:
            return "2R"
        return "2M"
    if tbsm < 22.9:
        return "3A" if ur > 73.2 else "3B"
    if tbsm < 25.0:
        return "4A" if ur > 70.3 else "4B"
    if tbsm < 27.0:
        return "5A" if ur > 68.7 else "5B"
    return "6A" if ur > 66.8 else "6B"


# ---------------------------------------------------------------------------
# Limiares dos níveis de desempenho
# ---------------------------------------------------------------------------
# ΔPHFT mínimo quando PHFT_ref < 70%: (a - b*PHFT_ref*100)/100
COEF_DELTA_PHFT = {
    "unifamiliar":            (45, 0.58),
    "multi_terreo":           (22, 0.21),
    "multi_tipo":             (28, 0.27),
    "multi_pilotis":          (28, 0.27),
    "multi_cobertura":        (18, 0.18),
}

# RedCgTT mínima: (intermediário_<100, intermediário_>=100,
#                  superior_<100,     superior_>=100)
RED_CGTT = {
    "unifamiliar":     (0.17, 0.27, 0.35, 0.55),
    "multi_terreo":    (0.15, 0.20, 0.30, 0.40),
    "multi_tipo":      (0.22, 0.25, 0.45, 0.50),
    "multi_pilotis":   (0.22, 0.25, 0.45, 0.50),
    "multi_cobertura": (0.15, 0.20, 0.30, 0.40),
}

PAVIMENTOS = ["Térreo", "Tipo", "Pilotis ou com subsolo", "Cobertura"]


def chave_tipologia(tipologia: str, pavimento: str) -> str:
    if tipologia.lower().startswith("uni"):
        return "unifamiliar"
    mapa = {
        "Térreo": "multi_terreo",
        "Tipo": "multi_tipo",
        "Pilotis ou com subsolo": "multi_pilotis",
        "Cobertura": "multi_cobertura",
    }
    return mapa[pavimento]


# ---------------------------------------------------------------------------
# Leitura dos outputs do EnergyPlus
# ---------------------------------------------------------------------------
PADRAO_COLUNA = re.compile(
    r"^(?P<chave>.*?):(?P<var>[^\[\]]+?)\s*\[(?P<unid>[^\]]*)\]\s*\((?P<freq>[^)]*)\)\s*$"
)

VARIAVEIS = {
    "ZONE OPERATIVE TEMPERATURE": "To",
    "ZONE IDEAL LOADS ZONE TOTAL COOLING ENERGY": "CgTR",
    "ZONE IDEAL LOADS ZONE TOTAL HEATING ENERGY": "CgTA",
}

SUFIXOS_EQUIPAMENTO = [
    " IDEAL LOADS AIR SYSTEM",
    " IDEAL LOADS AIRSYSTEM",
    " IDEALLOADS",
]


def _nome_zona(chave: str) -> str:
    z = chave.strip().upper()
    for suf in SUFIXOS_EQUIPAMENTO:
        if z.endswith(suf):
            z = z[: -len(suf)]
            break
    return z.strip()


def ler_saida_energyplus(arquivo, nome: str = "arquivo") -> pd.DataFrame:
    """
    Lê um CSV/XLSX de saída do EnergyPlus e devolve um DataFrame de colunas
    MultiIndex (zona, variável), com as variáveis já renomeadas para
    To / CgTR / CgTA.
    """
    if hasattr(arquivo, "read"):
        dados = arquivo.read()
        buffer = io.BytesIO(dados)
    else:
        with open(arquivo, "rb") as fh:
            buffer = io.BytesIO(fh.read())

    nome_lower = str(nome).lower()
    if nome_lower.endswith((".xlsx", ".xlsm", ".xls")):
        bruto = pd.read_excel(buffer)
    else:
        buffer.seek(0)
        bruto = pd.read_csv(buffer, sep=None, engine="python")

    colunas = {}
    for col in bruto.columns:
        m = PADRAO_COLUNA.match(str(col))
        if not m:
            continue
        var = VARIAVEIS.get(m.group("var").strip().upper())
        if var is None:
            continue
        colunas[(_nome_zona(m.group("chave")), var)] = pd.to_numeric(
            bruto[col], errors="coerce"
        )

    if not colunas:
        raise ValueError(
            f"Nenhuma variável reconhecida em '{nome}'. "
            "Esperado colunas como 'ZONA:Zone Operative Temperature [C](Hourly)'."
        )

    df = pd.DataFrame(colunas)
    df.columns = pd.MultiIndex.from_tuples(df.columns, names=["zona", "variavel"])
    return _para_horario(df)


def _para_horario(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega dados sub-horários em horários (média para To, soma para energia)."""
    n = len(df)
    if n == HORAS_ANO:
        return df.reset_index(drop=True)
    if n > HORAS_ANO and n % HORAS_ANO == 0:
        passo = n // HORAS_ANO
        grupo = df.index // passo
        partes = {}
        for (zona, var) in df.columns:
            s = df[(zona, var)].groupby(grupo)
            partes[(zona, var)] = s.mean() if var == "To" else s.sum()
        novo = pd.DataFrame(partes)
        novo.columns = pd.MultiIndex.from_tuples(novo.columns, names=["zona", "variavel"])
        return novo.reset_index(drop=True)
    raise ValueError(
        f"Esperadas {HORAS_ANO} linhas horárias (ou múltiplo); o arquivo tem {n}. "
        "Confira o RunPeriod e a frequência do Output:Variable."
    )


# ---------------------------------------------------------------------------
# Cálculo por APP e por UH
# ---------------------------------------------------------------------------
@dataclass
class APP:
    """Ambiente de permanência prolongada."""
    nome: str
    zona: str
    tipo: Literal["sala", "dormitorio", "misto"]
    area: float


@dataclass
class ResultadoAPP:
    nome: str
    phft: float
    cgtr: float          # kWh/ano
    cgta: float          # kWh/ano
    tomax: float
    tomin: float
    horas_ocupadas: int


@dataclass
class ResultadoUH:
    phft: float
    cgtr: float
    cgta: float
    cgtt: float
    tomax: float
    tomin: float
    area: float
    apps: list[ResultadoAPP] = field(default_factory=list)


def calcular_app(app: APP, to: pd.Series, cgtr: pd.Series | None,
                 cgta: pd.Series | None, intervalo: Intervalo) -> ResultadoAPP:
    """Aplica, hora a hora, as mesmas condições das colunas de cálculo da planilha."""
    n = len(to)
    ocup = perfil_ocupacao(app.tipo, n) > 0

    if intervalo.to_min is None:
        dentro = ocup & (to < intervalo.to_max)
    else:
        dentro = ocup & (to > intervalo.to_min) & (to < intervalo.to_max)

    horas_ocupadas = int(ocup.sum())
    phft = float(dentro.sum() / horas_ocupadas) if horas_ocupadas else 0.0

    soma_cgtr = 0.0
    if cgtr is not None:
        soma_cgtr = float(cgtr[ocup & (to >= intervalo.limite_cgtr)].sum()) / JOULE_POR_KWH

    soma_cgta = 0.0
    if intervalo.avalia_aquecimento and cgta is not None:
        soma_cgta = float(cgta[ocup & (to <= intervalo.limite_cgta)].sum()) / JOULE_POR_KWH

    to_ocupada = to[ocup]
    return ResultadoAPP(
        nome=app.nome,
        phft=phft,
        cgtr=soma_cgtr,
        cgta=soma_cgta,
        tomax=float(to_ocupada.max()) if horas_ocupadas else float("nan"),
        tomin=float(to_ocupada.min()) if horas_ocupadas else float("nan"),
        horas_ocupadas=horas_ocupadas,
    )


def consolidar_uh(resultados: list[ResultadoAPP], areas: list[float]) -> ResultadoUH:
    """
    Consolidação idêntica às macros da planilha:
    PHFT = média aritmética · cargas = soma · Tomáx = máximo · Tomín = mínimo.
    """
    if not resultados:
        raise ValueError("Nenhum APP informado.")
    return ResultadoUH(
        phft=sum(r.phft for r in resultados) / len(resultados),
        cgtr=sum(r.cgtr for r in resultados),
        cgta=sum(r.cgta for r in resultados),
        cgtt=sum(r.cgtr + r.cgta for r in resultados),
        tomax=max(r.tomax for r in resultados),
        tomin=min(r.tomin for r in resultados),
        area=sum(areas),
        apps=resultados,
    )


# ---------------------------------------------------------------------------
# Verificação dos níveis de desempenho
# ---------------------------------------------------------------------------
@dataclass
class Criterio:
    nome: str
    valor: float | None
    limite: float | None
    atende: bool | None
    texto: str


@dataclass
class Classificacao:
    minimo: bool
    intermediario: bool
    superior: bool
    nivel: str
    criterios: dict[str, list[Criterio]]
    delta_phft: float
    delta_phft_min: float
    red_cgtt: float
    red_cgtt_int: float
    red_cgtt_sup: float
    cgtt_por_area: float | None


def _fmt_pct(v):
    return "—" if v is None else f"{v * 100:.2f}%"


def classificar(ref: ResultadoUH, real: ResultadoUH, tipologia: str, pavimento: str,
                zona_bioclimatica: str) -> Classificacao:
    chave = chave_tipologia(tipologia, pavimento)

    lim_phft_min = 0.9 * ref.phft
    ok_phft_min = real.phft > lim_phft_min

    folga = 2.0 if (chave == "unifamiliar" or pavimento == "Cobertura") else 1.0
    lim_tomax = ref.tomax + folga
    ok_tomax = real.tomax <= lim_tomax

    # zona_bioclimatica: código novo (1R,1M,2R,2M,3A,3B,4A,4B,5A,5B,6A,6B) da
    # NBR 15220-3:2024. Ver nota em ZONAS_BIOCLIMATICAS sobre a fonte de
    # avalia_tomin.
    avalia_tomin = ZONAS_BIOCLIMATICAS[zona_bioclimatica].avalia_tomin
    lim_tomin = ref.tomin - 1.0
    ok_tomin = (real.tomin >= lim_tomin) if avalia_tomin else None

    minimo = ok_phft_min and ok_tomax and (ok_tomin is not False)

    # ---- ΔPHFT ------------------------------------------------------------
    a, b = COEF_DELTA_PHFT[chave]
    delta_phft_min = 0.0 if ref.phft >= 0.7 else (a - b * ref.phft * 100) / 100
    delta_phft = real.phft - ref.phft
    ok_delta = delta_phft >= delta_phft_min

    # ---- RedCgTT ----------------------------------------------------------
    if ref.area and ref.area > 0:
        cgtt_area = ref.cgtt / ref.area
        alto = cgtt_area >= 100
    else:
        cgtt_area = None
        alto = True  # sem área declarada, cai no limiar mais exigente

    int_baixo, int_alto, sup_baixo, sup_alto = RED_CGTT[chave]
    red_int = 0.0 if ref.phft < 0.7 else (int_alto if alto else int_baixo)
    red_sup = sup_alto if alto else sup_baixo
    red_cgtt = 1 - (real.cgtt / ref.cgtt) if ref.cgtt else 0.0

    # RedCgTT ≥ RedCgTTmín (tabela de critérios da norma). A planilha oficial
    # usa ">" estrito nas três abas de intervalo - divergência dela em
    # relação à norma, não repetida aqui.
    ok_red_int = red_cgtt >= red_int
    ok_red_sup = red_cgtt >= red_sup

    intermediario = minimo and ok_delta and ok_red_int
    superior = minimo and ok_delta and ok_red_sup
    # Regra do PHFT >= 95%: não é uma opção do usuário, é a própria norma
    # (11.4.4) - "o nível superior de desempenho térmico pode ser obtido se
    # o PHFTUH do modelo real for igual ou superior a 95%", desde que
    # também sejam atendidos os critérios de Tomáx/Tomín do nível mínimo.
    # Por isso é sempre aplicada, sem checkbox.
    if minimo and real.phft >= 0.95:
        superior = True

    if superior:
        nivel = "Superior"
    elif intermediario:
        nivel = "Intermediário"
    elif minimo:
        nivel = "Mínimo"
    else:
        nivel = "Não atende ao nível mínimo"

    criterios = {
        "Mínimo": [
            Criterio("PHFT UH", real.phft, lim_phft_min, ok_phft_min,
                     f"PHFT,real {_fmt_pct(real.phft)} > 0,9 × PHFT,ref = {_fmt_pct(lim_phft_min)}"),
            Criterio("Tomáx UH", real.tomax, lim_tomax, ok_tomax,
                     f"Tomáx,real {real.tomax:.2f} °C ≤ Tomáx,ref + {folga:.0f} = {lim_tomax:.2f} °C"),
            Criterio("Tomín UH", real.tomin, lim_tomin if avalia_tomin else None, ok_tomin,
                     (f"Tomín,real {real.tomin:.2f} °C ≥ Tomín,ref − 1 = {lim_tomin:.2f} °C"
                      if avalia_tomin
                      else f"Não avaliado na ZB {zona_bioclimatica}")),
        ],
        "Intermediário": [
            Criterio("ΔPHFT", delta_phft, delta_phft_min, ok_delta,
                     f"ΔPHFT {_fmt_pct(delta_phft)} ≥ {_fmt_pct(delta_phft_min)}"),
            Criterio("RedCgTT", red_cgtt, red_int, ok_red_int,
                     f"RedCgTT {_fmt_pct(red_cgtt)} ≥ {_fmt_pct(red_int)}"),
        ],
        "Superior": [
            Criterio("ΔPHFT", delta_phft, delta_phft_min, ok_delta,
                     f"ΔPHFT {_fmt_pct(delta_phft)} ≥ {_fmt_pct(delta_phft_min)}"),
            Criterio("RedCgTT", red_cgtt, red_sup, ok_red_sup,
                     f"RedCgTT {_fmt_pct(red_cgtt)} ≥ {_fmt_pct(red_sup)}"),
        ],
    }

    return Classificacao(
        minimo=minimo, intermediario=intermediario, superior=superior, nivel=nivel,
        criterios=criterios, delta_phft=delta_phft, delta_phft_min=delta_phft_min,
        red_cgtt=red_cgtt, red_cgtt_int=red_int, red_cgtt_sup=red_sup,
        cgtt_por_area=cgtt_area,
    )


st.set_page_config(page_title="NBR 15575 - Desempenho térmico", layout="wide")

AQUI = Path(__file__).parent


@st.cache_data
def carregar_climas() -> pd.DataFrame | None:
    """climas.csv é opcional: sem ele, o clima é informado manualmente.

    Valida se as zonas do arquivo são do esquema novo (NBR 15220-3:2024,
    1R a 6B) - um climas.csv antigo (zonas numéricas 1 a 8, de antes da
    atualização da ferramenta) não é compatível com ZONAS_BIOCLIMATICAS e
    quebraria a classificação mais adiante, então essas linhas são
    descartadas aqui, com aviso, em vez de deixar o erro estourar depois.
    """
    caminho = AQUI / "climas.csv"
    if not caminho.exists():
        return None
    try:
        df = pd.read_csv(caminho)
    except Exception:
        return None

    if "cidade" not in df.columns or "zona_bioclimatica" not in df.columns:
        st.sidebar.warning(
            "climas.csv não tem as colunas esperadas (cidade, "
            "zona_bioclimatica, ...) - ignorando o arquivo. Confira se é a "
            "versão nova, enviada junto com este streamlit_app.py."
        )
        return None

    zonas_validas = df["zona_bioclimatica"].astype(str).isin(ZONAS_BIOCLIMATICAS)
    if not zonas_validas.all():
        n_invalidas = int((~zonas_validas).sum())
        st.sidebar.warning(
            f"climas.csv tem {n_invalidas} cidade(s) com zona bioclimática "
            "fora do padrão NBR 15220-3:2024 (1R, 1M, 2R, 2M, 3A, 3B, 4A, "
            "4B, 5A, 5B, 6A, 6B) - essas linhas foram ignoradas. Isso "
            "costuma acontecer quando o climas.csv é de uma versão antiga "
            "da ferramenta; use o que veio junto com este streamlit_app.py."
        )
        df = df[zonas_validas].reset_index(drop=True)

    return df if not df.empty else None


def bloco_upload(rotulo: str, chave: str):
    """Par de uploads (VN + AC) para um modelo."""
    st.markdown(f"**{rotulo}**")
    vn = st.file_uploader(
        f"Ventilação natural - Zone Operative Temperature ({rotulo})",
        type=["csv", "xlsx", "xlsm"], key=f"vn_{chave}",
    )
    ac = st.file_uploader(
        f"Ar-condicionado - Zone Ideal Loads Total Heating/Cooling Energy ({rotulo})",
        type=["csv", "xlsx", "xlsm"], key=f"ac_{chave}",
    )
    return vn, ac


def carregar_modelo(vn_file, ac_file, rotulo: str):
    """Devolve (df_To, df_cargas) e a lista de zonas com temperatura operativa."""
    to = ler_saida_energyplus(vn_file, vn_file.name)
    zonas_to = sorted({z for z, v in to.columns if v == "To"})
    if not zonas_to:
        raise ValueError(f"{rotulo}: nenhuma Zone Operative Temperature encontrada.")

    cargas = ler_saida_energyplus(ac_file, ac_file.name)
    vars_carga = {v for _, v in cargas.columns}
    if not {"CgTR", "CgTA"} & vars_carga:
        raise ValueError(f"{rotulo}: nenhuma carga de Ideal Loads encontrada.")
    return to, cargas, zonas_to


def resultados_modelo(to, cargas, apps, intervalo):
    saidas, areas = [], []
    for app in apps:
        serie_to = to[(app.zona, "To")]
        serie_r = cargas[(app.zona, "CgTR")] if (app.zona, "CgTR") in cargas.columns else None
        serie_a = cargas[(app.zona, "CgTA")] if (app.zona, "CgTA") in cargas.columns else None
        saidas.append(calcular_app(app, serie_to, serie_r, serie_a, intervalo))
        areas.append(app.area)
    return consolidar_uh(saidas, areas)


def tabela_uh(res, titulo):
    st.markdown(f"**{titulo}**")
    st.dataframe(
        pd.DataFrame(
            {
                "Indicador": ["PHFT UH", "CgTR UH [kWh]", "CgTA UH [kWh]",
                              "CgTT UH [kWh]", "Tomáx,UH [°C]", "Tomín,UH [°C]"],
                "Valor": [f"{res.phft * 100:.2f}%", f"{res.cgtr:.2f}", f"{res.cgta:.2f}",
                          f"{res.cgtt:.2f}", f"{res.tomax:.2f}", f"{res.tomin:.2f}"],
            }
        ),
        hide_index=True, use_container_width=True,
    )


# ---------------------------------------------------------------------------
st.title("Classificação do desempenho térmico - NBR 15575-1")
st.caption("Método de simulação computacional")

climas = carregar_climas()

with st.sidebar:
    st.header("Unidade habitacional")

    opcoes = ["Escolher cidade", "Informar manualmente"] if climas is not None \
        else ["Informar manualmente"]
    if climas is None:
        st.caption("climas.csv não encontrado - informe TBSm e UR manualmente.")
    modo = st.radio("Definição do clima", opcoes)
    if modo == "Escolher cidade":
        # climas.csv traz as 29 cidades da NBR 15220-3:2024 (27 capitais +
        # Canela/RS e Petrolina/PE, as duas representativas que não são
        # capital) - não é a lista completa dos 5 507 municípios (essa está
        # na ABNT TR 15220-3-1, não normativa e não incluída aqui).
        cidade = st.selectbox("Cidade", climas["cidade"].tolist(),
                              index=int(climas.index[climas["cidade"] == "Florianopolis/SC"][0])
                              if "Florianopolis/SC" in climas["cidade"].values else 0)
        linha = climas[climas["cidade"] == cidade].iloc[0]
        zb = str(linha["zona_bioclimatica"])
    else:
        st.caption(
            "Sua cidade não está entre as 29 da tabela? Informe a TBSm e a "
            "UR médias anuais externas (do mesmo arquivo climático usado na "
            "simulação) que a zona é calculada pela regra da NBR 15220-3:2024."
        )
        tbsm = st.number_input("TBSm - temperatura média anual de bulbo seco externa [°C]",
                               value=22.0, step=0.1, format="%.2f")
        ur = st.number_input("UR - umidade relativa média anual externa [%]",
                             value=70.0, step=0.1, format="%.1f")
        with st.expander("Latitude/longitude (só é necessário perto do limite das zonas 1R/1M e 2R/2M)"):
            usar_coords = st.checkbox("Informar latitude/longitude", value=False)
            lat = st.number_input("Latitude [°] (negativa no hemisfério sul)", value=0.0, step=0.1) \
                if usar_coords else None
            lon = st.number_input("Longitude [°] (negativa a oeste de Greenwich)", value=0.0, step=0.1) \
                if usar_coords else None
        zb = classificar_zona_bioclimatica(tbsm, ur, lat, lon)

    info_zona = ZONAS_BIOCLIMATICAS.get(zb)
    if info_zona is None:
        st.error(
            f"Zona bioclimática '{zb}' não reconhecida (esperado: 1R, 1M, "
            "2R, 2M, 3A, 3B, 4A, 4B, 5A, 5B, 6A ou 6B). Se veio do "
            "climas.csv, confira se o arquivo é a versão nova da ferramenta."
        )
        st.stop()
    num_intervalo = info_zona.intervalo
    intervalo = INTERVALOS[num_intervalo]
    st.info(
        f"Zona bioclimática {zb} — {info_zona.descricao}\n\n"
        f"Intervalo {intervalo.numero} - {intervalo.descricao}"
    )

    tipologia = st.radio("Tipologia", ["Unifamiliar", "Multifamiliar"])
    pavimento = st.selectbox("Pavimento", PAVIMENTOS,
                             disabled=(tipologia == "Unifamiliar"))

col_ref, col_real = st.columns(2)
with col_ref:
    vn_ref, ac_ref = bloco_upload("Modelo de REFERÊNCIA", "ref")
with col_real:
    vn_real, ac_real = bloco_upload("Modelo REAL", "real")

if not all([vn_ref, ac_ref, vn_real, ac_real]):
    st.warning("Envie os quatro arquivos de saída do EnergyPlus para prosseguir.")
    st.stop()

try:
    to_ref, cargas_ref, zonas_ref = carregar_modelo(vn_ref, ac_ref, "Referência")
    to_real, cargas_real, zonas_real = carregar_modelo(vn_real, ac_real, "Real")
except ValueError as e:
    st.error(str(e))
    st.stop()

zonas_comuns = [z for z in zonas_ref if z in zonas_real]
if not zonas_comuns:
    st.error("Os modelos de referência e real não têm zonas com o mesmo nome.")
    st.stop()

st.subheader("Ambientes de permanência prolongada")
st.caption("Marque apenas os APP. Banheiros, circulações e cozinhas ficam de fora.")
st.caption(
    "Use **misto** para o APP que funciona como sala e dormitório no mesmo "
    "espaço (quitinetes, lofts, studios): a norma exige um padrão de "
    "ocupação próprio para ele (dormindo/descansando 00h-08h e 22h-24h, "
    "sentado/TV 14h-22h), diferente de marcá-lo apenas como sala ou só "
    "como dormitório."
)

config = pd.DataFrame({
    "Zona": zonas_comuns,
    "É APP": [True] * len(zonas_comuns),
    "Tipo": ["dormitorio"] * len(zonas_comuns),
    "Área útil [m²]": [0.0] * len(zonas_comuns),
})
editado = st.data_editor(
    config, hide_index=True, use_container_width=True,
    column_config={
        "Zona": st.column_config.TextColumn(disabled=True),
        "Tipo": st.column_config.SelectboxColumn(options=["sala", "dormitorio", "misto"]),
        "Área útil [m²]": st.column_config.NumberColumn(min_value=0.0, step=0.5),
    },
)

apps = [
    APP(nome=r["Zona"], zona=r["Zona"], tipo=r["Tipo"], area=float(r["Área útil [m²]"]))
    for _, r in editado.iterrows() if r["É APP"]
]

if not apps:
    st.warning("Selecione ao menos um APP.")
    st.stop()

if sum(a.area for a in apps) <= 0:
    st.error(
        "Informe a área útil dos APP. Sem ela não dá para calcular CgTT,ref/área, "
        "que define se a redução exigida no nível superior é de 35% ou 55%."
    )
    st.stop()

res_ref = resultados_modelo(to_ref, cargas_ref, apps, intervalo)
res_real = resultados_modelo(to_real, cargas_real, apps, intervalo)
cls = classificar(res_ref, res_real, tipologia, pavimento, zb)

st.divider()
c1, c2 = st.columns(2)
with c1:
    tabela_uh(res_ref, "RESULTADOS PARA A UH REF")
with c2:
    tabela_uh(res_real, "RESULTADOS PARA A UH REAL")

st.subheader("Nível de desempenho")
cor = {"Superior": "🟢", "Intermediário": "🔵", "Mínimo": "🟡"}.get(cls.nivel, "🔴")
st.markdown(f"## {cor} {cls.nivel}")

linhas = []
for nivel, criterios in cls.criterios.items():
    for k in criterios:
        linhas.append({
            "Nível": nivel,
            "Critério": k.nome,
            "Verificação": k.texto,
            "Atende": "—" if k.atende is None else ("Sim" if k.atende else "Não"),
        })
st.dataframe(pd.DataFrame(linhas), hide_index=True, use_container_width=True)

with st.expander("Resultados por APP"):
    for rotulo, res in (("Referência", res_ref), ("Real", res_real)):
        st.markdown(f"**{rotulo}**")
        st.dataframe(pd.DataFrame([{
            "APP": a.nome,
            "PHFTapp": f"{a.phft * 100:.2f}%",
            "CgTR [kWh]": f"{a.cgtr:.2f}",
            "CgTA [kWh]": f"{a.cgta:.2f}",
            "Tomáx [°C]": f"{a.tomax:.2f}",
            "Tomín [°C]": f"{a.tomin:.2f}",
            "Horas ocupadas": a.horas_ocupadas,
        } for a in res.apps]), hide_index=True, use_container_width=True)

with st.expander("Parâmetros aplicados"):
    st.write({
        "Faixa de PHFT": (f"To < {intervalo.to_max} °C" if intervalo.to_min is None
                          else f"{intervalo.to_min} °C < To < {intervalo.to_max} °C"),
        "CgTR contabilizada quando": f"To ≥ {intervalo.limite_cgtr} °C",
        "CgTA contabilizada quando": (f"To ≤ {intervalo.limite_cgta} °C"
                                      if intervalo.avalia_aquecimento else "não avaliada"),
        "CgTT,ref / área": (f"{cls.cgtt_por_area:.1f} kWh/(ano·m²)"
                            if cls.cgtt_por_area else "—"),
        "ΔPHFT mínimo": f"{cls.delta_phft_min * 100:.2f}%",
        "RedCgTT mínima (intermediário)": f"{cls.red_cgtt_int * 100:.2f}%",
        "RedCgTT mínima (superior)": f"{cls.red_cgtt_sup * 100:.2f}%",
    })

st.download_button(
    "Baixar resultados (CSV)",
    pd.DataFrame(linhas).to_csv(index=False).encode("utf-8"),
    file_name="nbr15575_resultados.csv", mime="text/csv",
)
