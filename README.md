# Classificação NBR 15575-1 (método de simulação)

## Entradas

São **quatro** arquivos. A classificação compara o modelo real com o de referência,
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

**Faixas por intervalo**

| Intervalo | Faixa do PHFT | CgTR conta quando | CgTA |
|---|---|---|---|
| 1 — TBSm < 25 °C | 18 °C < To < 26 °C | To ≥ 26 °C | To ≤ 18 °C |
| 2 — 25 ≤ TBSm < 27 °C | To < 28 °C | To ≥ 28 °C | não avaliada |
| 3 — TBSm ≥ 27 °C | To < 28 °C | To ≥ 30 °C | não avaliada |

**Níveis**

| Tipologia | Interm. <100 | Interm. ≥100 | Superior <100 | Superior ≥100 |
|---|---|---|---|---|
| Unifamiliar | 17% | 27% | 35% | 55% |
| Multi · térreo | 15% | 20% | 30% | 40% |
| Multi · tipo/pilotis | 22% | 25% | 45% | 50% |
| Multi · cobertura | 15% | 20% | 30% | 40% |

No nível intermediário, `RedCgTTmín` cai a zero quando `PHFT,ref < 70%`.
