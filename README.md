# Avaliação NBR 15575-1 — método de simulação

Conversão da `Planilha_NBR15575_Oficial_R02.xlsm` (LabEEE/UFSC) para Python + Streamlit.

## Arquivos

| Arquivo | Papel |
|---|---|
| `app.py` | Interface Streamlit |
| `nbr15575_core.py` | Cálculo puro — sem Streamlit, importável e testável |
| `climas.csv` | 411 cidades → zona bioclimática e intervalo, extraídas da aba `Climas` |

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Entradas

São **quatro** arquivos, e não dois. A classificação compara o modelo real com o de referência,
então cada modelo precisa das duas simulações:

| Modelo | Simulação | Output do EnergyPlus |
|---|---|---|
| Referência | ventilação natural | `Zone Operative Temperature` |
| Referência | ar-condicionado | `Zone Ideal Loads Zone Total Cooling Energy` e `... Total Heating Energy` |
| Real | ventilação natural | `Zone Operative Temperature` |
| Real | ar-condicionado | as duas cargas |

Aceita `.csv`, `.xlsx` e `.xlsm`. Os cabeçalhos são lidos no formato nativo do EnergyPlus:

```
THERMAL ZONE 2:Zone Operative Temperature [C](Hourly)
THERMAL ZONE 2 IDEAL LOADS AIR SYSTEM:Zone Ideal Loads Zone Total Cooling Energy [J](Hourly)
```

O sufixo `IDEAL LOADS AIR SYSTEM` é removido para casar a carga com a zona. Saídas sub-horárias
são agregadas para 8.760 horas — média para temperatura, soma para energia.

## Lógica replicada

**Ocupação** (fixa, idêntica em todos os dias do ano, como na planilha):

- Sala/estar: 14h–22h → 2.920 h/ano
- Dormitório: 00h–08h e 22h–24h → 3.650 h/ano

**Por APP**

- `PHFTapp` = horas ocupadas dentro da faixa ÷ horas ocupadas
- `CgTRapp` = Σ carga de refrigeração nas horas ocupadas acima do limite ÷ 3.600.000
- `CgTAapp` = Σ carga de aquecimento nas horas ocupadas com To ≤ 18 °C ÷ 3.600.000
- `Tomáx` e `Tomín` considerando apenas horas ocupadas

**Faixas por intervalo**

| Intervalo | Faixa do PHFT | CgTR conta quando | CgTA |
|---|---|---|---|
| 1 — TBSm < 25 °C | 18 °C < To < 26 °C | To ≥ 26 °C | To ≤ 18 °C |
| 2 — 25 ≤ TBSm < 27 °C | To < 28 °C | To ≥ 28 °C | não avaliada |
| 3 — TBSm ≥ 27 °C | To < 28 °C | To ≥ 30 °C | não avaliada |

**Consolidação da UH** (conforme as macros `AtualizaPHFTIntervalo1/23`)

PHFT é média aritmética entre os APP; cargas são somadas; Tomáx é o maior entre os APP e
Tomín o menor; a área é a soma das áreas úteis.

**Níveis**

- Mínimo: `PHFT,real ≥ 0,9 × PHFT,ref`; `Tomáx,real ≤ Tomáx,ref + 2` (unifamiliar ou cobertura)
  ou `+ 1` (multifamiliar); `Tomín,real ≥ Tomín,ref − 1`, só nas ZB 1 a 4
- Intermediário e superior: `ΔPHFT ≥ ΔPHFTmín` e `RedCgTT > RedCgTTmín`

`ΔPHFTmín` vale 0 quando `PHFT,ref ≥ 70%`; abaixo disso segue `(a − b × PHFT,ref × 100)/100`,
com (45; 0,58) para unifamiliar, (22; 0,21) para multifamiliar térreo, (28; 0,27) para tipo e
pilotis, (18; 0,18) para cobertura.

`RedCgTTmín` depende da tipologia e de `CgTT,ref/área` estar abaixo ou acima de
100 kWh/(ano·m²):

| Tipologia | Interm. <100 | Interm. ≥100 | Superior <100 | Superior ≥100 |
|---|---|---|---|---|
| Unifamiliar | 17% | 27% | 35% | 55% |
| Multi · térreo | 15% | 20% | 30% | 40% |
| Multi · tipo/pilotis | 22% | 25% | 45% | 50% |
| Multi · cobertura | 15% | 20% | 30% | 40% |

No nível intermediário, `RedCgTTmín` cai a zero quando `PHFT,ref < 70%`.

## Validação

Os critérios foram conferidos contra dois casos já rodados na planilha original
(UH unifamiliar térrea, ZB 3, três APP, `PHFT,ref` 62,03%, `CgTT,ref` 4.120 kWh):

| Caso | PHFT real | CgTT real | Veredito da planilha | Veredito do código |
|---|---|---|---|---|
| leve, 90% | 69,81% | 3.301 | Mínimo | Mínimo |
| brise deslocado | 75,50% | 2.782 | Não atende ao mínimo | Não atende ao mínimo |

Em ambos, cada linha da tabela de atendimento coincide, inclusive `ΔPHFTmín` = 9,02%.

## Três divergências que você precisa conhecer

**1. Intervalo 3 provavelmente tem um erro de copiar e colar na planilha.**
A aba `TBSm >= 27ºC` usa `To < 28 °C` para o PHFT e `To ≥ 30 °C` para a carga de refrigeração —
dois limites diferentes para a mesma faixa. O cabeçalho da coluna também ficou como
"Temperatura operativa menor que 28ºC", igual ao do intervalo 2. Pela norma, a faixa do
intervalo 3 é definida por 30 °C. **O código reproduz a planilha**, mas o limite está isolado em
`INTERVALOS[3]` dentro de `nbr15575_core.py`: troque `to_max` para `30.0` se quiser seguir a
norma. Não afeta Florianópolis, que é intervalo 1.

**2. Área dos APP em branco muda o resultado silenciosamente.**
Na planilha, `CGTT/ÁREA` é `IFERROR(C9/C12,"")`. Com a área zerada o resultado é texto vazio, e
`"">=100` no Excel devolve VERDADEIRO — texto é sempre maior que número. O efeito é que a
redução exigida no nível superior salta de 35% para 55% sem nenhum aviso. Foi o que aconteceu
nos seus slides, que estão com ÁREA APP = 0,0. **Aqui o app bloqueia e pede a área.**

**3. O ramo do PHFT ≥ 95% é inalcançável na planilha.**
Em `T13`, a segunda condição já devolve "Não Atende" antes de o teste de 95% ser avaliado, então
ele só dispara em casos-limite. Deixei como caixa de seleção na barra lateral, desmarcada por
padrão — assim o comportamento é idêntico ao da planilha, e você decide se quer aplicar a regra
como alternativa explícita.

## Fora do escopo

As abas `(misto)` da planilha, para UH com ambientes de condicionamento misto, não foram
convertidas. Se você precisar delas, dá para estender `INTERVALOS` e `calcular_app`.
