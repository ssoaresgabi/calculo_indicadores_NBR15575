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

