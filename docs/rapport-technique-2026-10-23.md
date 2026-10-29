# Rapport technique — Lecture multi-marques d'automates depuis Python

**Date :** 23/10/2026
**Objet :** Lire les données vives (et, à terme, la logique) d'automates de plusieurs marques depuis un PC, sans passer par le logiciel constructeur, en vue d'un outil de diagnostic multi-marques.
**Dépendances principales :** `pyserial`, `libnodave` (Siemens), Python 32 bits pour la partie Siemens.

---

## 1. Vue d'ensemble

En une session, trois marques ont été lues en direct depuis du code Python maison :

| Automate | Protocole | Liaison | Décodage des mots | État |
|---|---|---|---|---|
| Panasonic FP0R C16T / C16P | MEWTOCOL-COM | série (port outil) | little-endian | **validé** (travail antérieur) |
| Siemens S7-300 | S7 via S7ONLINE | PC Adapter USB (6GK1571) | big-endian, adressé octet | **validé ce jour** |
| Omron CJ1M CPU11/12 | Host Link (C-mode) | série (port périphérique, FTDI) | hex, chiffre de poids fort en premier | **validé ce jour** |

Chaque pilote partage le même schéma : envoi avec reprise, vérification de l'en-tête **et** de la longueur, contrôle du checksum sur la réponse, puis décodage. Seul le pilote de protocole change ; le moteur (table de valeurs attendues → polling → comparaison) reste commun.

---

## 2. Panasonic FP0R (MEWTOCOL) — rappel

Déjà documenté séparément. Points clés repris ici car ils servent de modèle :

- Trame `%01#RDD...` + BCC (XOR de `%` jusqu'à la fin des données, 2 hex) + CR.
- Réponse OK `%01$RD...`, erreur `%01!xx`.
- **Bug corrigé :** l'automate ne renvoie qu'environ 28 registres par trame ; au-delà, la réponse est coupée sans erreur. Correctif : lecture par blocs ≤ 24 registres **et** rejet si le nombre de mots reçus ≠ demandé.
- Le C16P (sorties PNP) est identique au C16T (sorties NPN) côté protocole : tout le code FP0R fonctionne tel quel.
- Point à renforcer (voir §6) : le BCC de la réponse est retiré mais **pas vérifié**.

---

## 3. Siemens S7-300 — accès par S7ONLINE

### 3.1 Le problème de départ
Le S7-300 (ici) communique en **MPI** (bus RS-485, 187,5 kbaud, passage de jeton), pas en ASCII série. On ne peut donc pas ouvrir un port COM et envoyer des trames à la main.

### 3.2 Matériel passé en revue
- **PC Adapter USB 6GK1571-0BA00-0AA0** : fonctionne dans STEP 7, mais **ne crée pas de port COM** et parle un protocole USB propriétaire. `libnodave` ne le pilote pas directement.
- **Câble PC/PPI à boîtier (8 DIP)** : donne bien un port COM (COM6), mais il est **verrouillé en PPI** (protocole S7-200). Le S7-300 parle MPI → ce câble ne l'atteint pas. Confirmé dans STEP 7 : onglet propriétés PPI uniquement, 9,6 kbaud, pas d'option MPI.

### 3.3 La solution retenue
Ne pas piloter l'adaptateur en direct, mais **passer par le pilote Siemens S7ONLINE** (`s7onlinx.dll`) que STEP 7 utilise déjà. `libnodave` sait emprunter ce même pilote sur Windows (transport **S7online**). Chaîne :

```
code Python → libnodave → S7ONLINE → PC Adapter USB → S7-300
```

### 3.4 Mise en œuvre validée
- Interface réglée sur **`USB.S7USB.1`** dans « Régler l'interface PG/PC ».
- **Fermer STEP 7** avant d'exécuter le code (un seul maître à la fois sur l'interface).
- **Python 32 bits** obligatoire (la DLL `s7onlinx.dll` est 32 bits).
- Décodage **big-endian**, adressage **par octet** (`DB10.DBW20` = octets 20–21, octet fort d'abord).

### 3.5 Résultat
Lecture de MB0–MB3, puis écriture d'un témoin depuis STEP 7 (MW0 = 1234) relue correctement par le script :
```
bytes : 04 D2 00 00
MW0   : 1234
```
→ pilote S7-300 prouvé de bout en bout.

### 3.6 Remarques terrain
- La lecture a fonctionné même commutateur CPU sur **RUN** (l'erreur « Function not permitted in current protection level » ne touchait que l'**écriture**). L'outil de diagnostic pourra donc lire sans toucher au niveau de protection du client.
- Laisser le commutateur dans la position trouvée en arrivant.

---

## 4. Omron CJ1M — Host Link (C-mode)

### 4.1 Liaison
- Câble sur le **port périphérique** (et non le port RS-232C), via adaptateur **FTDI** → COM6.
- Le port périphérique était en **Toolbus** (protocole CX-Programmer). Il faut le mettre en **Host Link**.

### 4.2 Réglages automate
- Onglet **Port périphérique** des Paramètres : **Standard (9600 ; 1,7,2,E)**, Mode **Host Link**, unité **0**.
- **DIP 4 ON** pour que le port périphérique applique ces réglages au lieu de l'auto-détection Toolbus.
- Pour reconnecter CX-Programmer ensuite : **SYSMAC WAY** (9600, 7, E, 2), pas Toolbus.
- Secours : **DIP 4 OFF** → retour au Toolbus auto, CX-Programmer reconnecte comme avant.

### 4.3 Trame
```
@ 00 RD 0000 0004 FCS * CR     (lire D0..D3)
```
- Début `@`, n° station 2 chiffres, en-tête `RD` (lire DM), adresse 4 chiffres, nombre de mots 4 chiffres.
- **FCS** = XOR de `@` jusqu'au dernier caractère de texte, 2 hex — identique dans l'idée au BCC Panasonic. Le code `fcs()` reprend `bcc()` tel quel.
- Fin : `*` + CR.
- Réponse : `@00RD` + code de fin (`00` = OK) + données + FCS + `*` CR.

### 4.4 Décodage — correction importante
Contrairement au FP0R, **pas d'inversion d'octets**. Chaque mot arrive en 4 hex, chiffre de poids fort en premier : `int("04D2", 16)`. Les valeurs 32 bits restent mot faible d'abord (comme le FP0R).

### 4.5 Résultats
- Test d'écho `TS` : écho exact, FCS valide → liaison + réglages + checksum prouvés.
- Lecture sur données réelles du programme en place (lecture seule, non 1234) :
```
D0 = 62909 (hex F5BD)   ← concorde avec CX-Programmer (-2627 signé = F5BD)
D1 = 27741 (hex 6C5D)
D2 = 48391 (hex BD07)
D3 = 53725 (hex D1DD)
```
- Le pilote Host Link vérifie le **FCS de la réponse** avant d'accepter les données (contrôle qui manquait au FP0R).

### 4.6 Incidents rencontrés et réglés
- **Erreur « bus E/S » / voyant ERR** : capot de fin **CJ1W-TER01 manquant** sur le rack. Sans lui, erreur de bus et refus de créer la table E/S. À remettre hors tension, puis **Options → Créer** la table E/S.
- **Table E/S** faisait référence à une ancienne carte C200H ASCII absente → à reconstruire depuis le matériel réel.

---

## 5. Lecture du programme (logique) — état de la question

Objectif visé : lire la **logique** dans l'automate, pas seulement les valeurs. Ce qui a été établi :

- **Faisabilité par marque, inégale.**
  - **Siemens S7-300 :** l'upload de blocs fait partie du protocole S7 (c'est ce que fait « charger depuis l'API » dans STEP 7). Le résultat est du **bytecode MC7**, qui se rapproche de l'STL. Prior art open-source : **DotNetSiemensPLCToolBoxLibrary** (C#, LGPL) convertit MC7 → AWL/STL ; décompilateur commercial JEB. Noms de variables et commentaires **perdus** (ils vivent dans le projet, pas dans l'automate).
  - **Omron / Panasonic :** l'encodage du programme n'est pas documenté publiquement.

- **Fichier `.fp` du C16P — observations structurelles** (fait pendant la session, avant arrêt du décodage) :
  - Taille 24 643 octets. En-tête commençant par `FP`, puis zone paramètres / registres système dans les ~0x4A0 premiers octets.
  - **Zone programme** à partir d'environ **0x4A0** : flux de **mots 16 bits little-endian**, l'octet de poids fort se regroupant sur un petit ensemble de valeurs (`F8`, `FA`, `FB`, `FC`, `FD`, `A9`…) — signature d'une table d'opcodes (octet = classe d'instruction, suivi des opérandes).
  - Bloc séparé en fin de fichier (~0x5F00+), riche en `FF` — vraisemblablement la **table commentaires / symboles**.
  - Le programme exemple a été obtenu en clair sous forme de **ladder** (PDF FPWIN GR) : CMP, DMV, MV, TMY, TML, F35 +1, BKMV, CALL/SUB, etc. — sert de « corrigé » pour un éventuel décodage différentiel.

- **Limites posées / non franchies dans cette session :**
  - Le **décodage octet-à-octet du format compilé** (reconstituer l'instruction à partir des octets) et la construction d'un outil qui **extrait le programme d'un automate sans le logiciel constructeur** n'ont **pas** été réalisés ici — assistance interrompue sur cette partie. Rien n'a été produit sur l'encodage des opcodes.
  - **Automates protégés par mot de passe :** hors sujet — on ne contourne pas les protections des automates clients. Voie légitime : obtenir le mot de passe du client / constructeur.

- **Voie alternative identifiée (non retenue par l'utilisateur, consignée pour mémoire) :** les logiciels constructeurs **exportent** le programme en texte (mnémoniques FPWIN GR, mnémoniques CX-Programmer, source STL STEP 7). Parser un export texte donne la même logique sans dépendre du format binaire interne.

---

## 6. À faire / points de vigilance

1. **Vérifier le checksum de la réponse partout.** Le pilote Omron le fait ; l'ajouter au FP0R (le BCC de réponse est aujourd'hui ignoré). Un cadre corrompu mais contenant le bon en-tête passerait sinon.
2. **32 bits pour Siemens uniquement.** Prévoir la partie S7 comme **processus séparé 32 bits** piloté par l'app principale, pour ne pas imposer le 32 bits à tout l'outil.
3. **Décodages distincts par marque** — ne pas réutiliser le `words()` little-endian du FP0R pour Siemens (big-endian) ni Omron (hex poids fort d'abord). Isoler chaque décodeur.
4. **Réglages de port variables par site** — chaque table client devrait noter port, vitesse, format, n° d'unité. Auto-détection possible via les sondes « es-tu là ? » : `TS` (Omron), lecture d'état MEWTOCOL (Panasonic), ouverture S7ONLINE + MPI 2 (Siemens).
5. **Sauvegarder le programme avant toute manip** sur un automate en service, surtout après avoir touché la table E/S.
6. **Ne pas écrire dans des DT utilisés par le programme** pendant les tests (risque de fausser le test ou de perturber la logique).

---

## 7. Fichiers produits

| Script | Rôle |
|---|---|
| `s7read.py` | S7-300 via S7ONLINE : ouvre, connecte, lit MB0–MB3, décode big-endian. |
| `hl_test.py` | Omron : test d'écho `TS` (valide liaison + FCS). |
| `hl_read.py` | Omron : lecture DM par `RD`, contrôles FCS/longueur/code de fin, décodage hex. |

*(Les scripts FP0R — peaks, diag, scan, write_dt — proviennent du travail antérieur.)*
