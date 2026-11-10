# Documentation

Start with the data model: it is the reference for what the agent reads and checks.

| Document | For | Status |
|---|---|---|
| [data-model/DATA_MODEL.md](data-model/DATA_MODEL.md) | Everyone | **Current.** Data model v3: the 6 sources, the reconciled position, the 13 rules, the seed and the 10 scenarios |
| [data-model/EVENT_CONTRACT.md](data-model/EVENT_CONTRACT.md) | Engineers, Bimbo IT | **Current.** Event contract v4: entities, columns, the simulated feed and the assumptions to confirm |
| [data-model/Data_Inconsistencies_9Nov.pdf](data-model/Data_Inconsistencies_9Nov.pdf) | Bimbo business team | **Current.** The workbook's own inconsistencies, with values, and how the seed differs from the workbook |
| [guides/USER_GUIDE.md](guides/USER_GUIDE.md) | Planners, supervisors, business readers | Partly v2: the worklist is described correctly, the rule list is not. Due for a rewrite |
| [guides/SYSTEMS_GUIDE.md](guides/SYSTEMS_GUIDE.md) | Everyone using the live simulation | Partly v2: the two apps are described correctly, the dummy data is not. Due for a rewrite |
| [engineering/TECHNICAL_OVERVIEW.md](engineering/TECHNICAL_OVERVIEW.md) | Engineers, IT | Partly v2: stack, web layer and security are current, the engine section is not. Due for a rewrite |
| [engineering/DEPLOYMENT.md](engineering/DEPLOYMENT.md) | Operations | **Current.** Server, HTTPS, accounts, versions and releases |
| [engineering/REPOSITORY_HISTORY.md](engineering/REPOSITORY_HISTORY.md) | Engineers | **Current.** Branches, versions, and the 10 Nov overwrite of `main` |

Where a partly-v2 document and the data model disagree, the data model is right.

## Folders

- `data-model/`: what the agent reads and checks.
- `guides/`: how to use the worklist and the source simulator (`guides/images/`: screenshots).
- `engineering/`: how the application is built, deployed, versioned and released.
- `archive/v2/`: documents for the v2 data model (the five client extracts of 12 Sep 2026, 19 rules),
  kept for reference and no longer maintained: the client alignment review, the code review and its
  prompt, the application guide, the data flow and mapping guides, and the v2 simulation data.
- `reference/`: the client's requirement brief and deck. Client material: not committed (`.gitignore`).

The release history is in [../CHANGELOG.md](../CHANGELOG.md).
