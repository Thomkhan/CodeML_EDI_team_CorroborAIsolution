# Modèle de segmentation (Palier 2)

Déposez ici le modèle converti par `notebook/train_unet.ipynb` :

```
public/model/
  model.json
  group1-shard1of1.bin
```

Tant que `model.json` est absent, l'app le détecte et utilise le chemin
classique (détection du bord + tracé radial) sans rien signaler à
l'utilisateur — c'est volontaire : le brief demande qu'une mesure fiable sans IA
passe avant une IA sans mesure.

Après dépôt, vérifiez de bout en bout :

```bash
npm run build
npm run harness && python3 scripts/eval_mm.py --synthetic 40
```
