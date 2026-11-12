# Classificação NBR 15575-1 (método de simulação)

## Entradas

São **quatro** arquivos. A classificação compara o modelo real com o de referência,
então cada modelo precisa das duas simulações:

| Modelo | Simulação | Output do EnergyPlus |
|---|---|---|
| Referência | ventilação natural | `Zone Operative Temperature` |
| Referência | ar-condicionado | `Zone Ideal Loads Zone Total Cooling Energy` e `Zone Ideal Loads Zone Total Heating Energy` |
| Real | ventilação natural | `Zone Operative Temperature` |
| Real | ar-condicionado | `Zone Ideal Loads Zone Total Cooling Energy` e `Zone Ideal Loads Zone Total Heating Energy` |

Aceita `.csv`, `.xlsx` e `.xlsm`. Os cabeçalhos são lidos no formato nativo do EnergyPlus:

```
THERMAL ZONE 2:Zone Operative Temperature [C](Hourly)
THERMAL ZONE 2 IDEAL LOADS AIR SYSTEM:Zone Ideal Loads Zone Total Cooling Energy [J](Hourly)
```


## Arquivo SQL do EnergyPlus (recomendado para multifamiliares)

Inclua no IDF:

```
Output:SQLite, SimpleAndTabular;
```

e envie o `eplusout.sql` de cada uma das quatro simulações. Com o SQL, a ferramenta:

- lê nome, área de piso e posição de cada zona (tabela `Zones`);
- agrupa as zonas em UHs pelo nome (`T1_AP101_SALA` → UH `T1_AP101`, ambiente `SALA`)
  e sugere o tipo de APP (SALA/ESTAR → sala, DORM/QUARTO/SUITE → dormitório,
  LOFT/STUDIO → misto);
- separa as torres pela posição em planta (ou pelo nome, se ele trouxer `T1`, `TORRE2`,
  `BLOCO A`...) e numera os pavimentos pela cota;
- deixa escolher, em cada torre, o térreo, **um** pavimento tipo e a cobertura, e só lê
  os resultados horários das zonas desses pavimentos.

Tudo o que foi detectado pode ser corrigido nas tabelas antes do cálculo.
