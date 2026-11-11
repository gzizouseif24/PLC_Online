"use strict";
// Page language: French (as written in the code) or English.
// The page text stays French in app.js and the server; in English every text node and
// title / placeholder / label attribute is translated on the fly (exact text, then
// patterns for messages with numbers, then known phrases inside longer messages).

const I18N = (() => {
  const strip = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "");
  const norm = (s) => strip(s).replace(/\s+/g, " ").trim();

  // ---- exact texts (also used as phrases inside longer messages) ----
  const EN = {
    // top, connection
    "Non connecte": "Not connected", "Pas connecte": "Not connected", "Connexion": "Connection",
    "Automate": "PLC", "Connecter": "Connect", "Deconnecter": "Disconnect", "Reconnecter": "Reconnect",
    "Deconnecte.": "Disconnected.", "Echec de connexion": "Connection failed", "Liaison perdue": "Link lost",
    "Langue": "Language",
    "Schneider TSX Micro / Premium": "Schneider TSX Micro / Premium",
    "Demo (S7-300 simule)": "Demo (simulated S7-300)", "Demo (TSX simule)": "Demo (simulated TSX)",
    "WAGO 750 (coupleur Ethernet)": "WAGO 750 (Ethernet coupler)",
    "Demo (WAGO 750-352 simule)": "Demo (simulated WAGO 750-352)",
    "Demo (Delta DVP28SV simule)": "Demo (simulated Delta DVP28SV)",
    "(bientot)": "(coming soon)",
    "Adresse MPI": "MPI address", "Rack": "Rack", "Emplacement CPU": "CPU slot", "Port COM": "COM port",
    "Adresse(s) du PC": "PC address(es)", "Adresse IP du coupleur": "Coupler IP address",
    "Mode (ascii ou rtu)": "Mode (ascii or rtu)", "Vitesse": "Baud rate", "Parite (E, O ou N)": "Parity (E, O or N)",
    "Station": "Station",
    "S7-300 : STEP 7 installe, interface PG/PC sur": "S7-300: STEP 7 installed, PG/PC interface set to",
    ", SIMATIC Manager ferme.": ", SIMATIC Manager closed.",
    "TSX : cable": "TSX: cable",
    "sur la prise TER, selecteur dans la meme position qu'avec PL7,": "on the TER port, selector in the same position as with PL7,",
    "PL7 ferme": "PL7 closed", ". 9600 bauds, 8 bits, parite impaire.": ". 9600 baud, 8 bits, odd parity.",
    "Delta : adaptateur RS-485 sur COM2 (+ / -) ou cable RS-232 sur COM1 (mini-DIN),":
      "Delta: RS-485 adapter on COM2 (+ / -) or RS-232 cable on COM1 (mini-DIN),",
    "WPLSoft / ISPSoft ferme": "WPLSoft / ISPSoft closed",
    ". Defaut Delta : ASCII 9600 7E1, station 1. Reglages inconnus : python tools/delta.py chercher.":
      ". Delta default: ASCII 9600 7E1, station 1. Unknown settings: python tools/delta.py chercher.",
    "WAGO : cable Ethernet entre le PC et le coupleur, puis": "WAGO: Ethernet cable between the PC and the coupler, then",
    "Recherche automatique": "Automatic search", "Carte reseau du PC (cote coupleur)": "PC network card (coupler side)",
    "Balayer les reseaux courants": "Sweep common networks", "Adresse IP du PC": "PC IP address",
    "Mettre cette adresse": "Set this address", "Remettre en automatique": "Back to automatic",
    "Fenetre administrateur Windows a accepter. \"Remettre en automatique\" : a faire en partant, pour que le PC retrouve le reseau de l'usine / internet.":
      "A Windows administrator prompt must be accepted. \"Back to automatic\": do it when leaving, so the PC gets the plant network / internet back.",
    "Le PC prend une adresse dans chaque reseau courant (192.168.0/1/2.x, 10.0.0.x...) le temps du balayage. Demande les droits administrateur.":
      "The PC takes an address in each common network (192.168.0/1/2.x, 10.0.0.x...) during the sweep. Needs administrator rights.",
    "Chercher": "Search", "Connectez un automate pour voir son etat.": "Connect a PLC to see its status.",
    "Sans automate : choisir": "Without a PLC: choose", "Demo": "Demo",
    // tabs
    "Etat": "Status", "Diagnostic": "Diagnostics", "Surveillance": "Watch", "Programme": "Program", "Donnees": "Data",
    // data / program
    "Note (facultatif)": "Note (optional)", "ex. recette client avant reglage": "e.g. customer recipe before adjustment",
    "ex. avant modification convoyeur 2": "e.g. before conveyor 2 change",
    "Sauvegarder les donnees de l'automate": "Back up the PLC data",
    "Copie de tous les mots internes %MW et des constantes %KW (recettes, compteurs, reglages), sans le programme.":
      "Copy of all %MW internal words and %KW constants (recipes, counters, settings), without the program.",
    "Copie de tous les registres D (recettes, compteurs, reglages), sans le programme.":
      "Copy of all D registers (recipes, counters, settings), without the program.",
    "Fichier deja sur le PC (transfert de donnees PL7 .dat, ou .csv) :": "File already on the PC (PL7 data transfer .dat, or .csv):",
    "Fichier deja sur le PC (.csv de cet outil) :": "File already on the PC (.csv from this tool):",
    "Importer un .dat ou .csv": "Import a .dat or .csv", "Importer un .csv": "Import a .csv",
    "Donnees sur ce PC": "Data on this PC", "Date": "Date", "Note": "Note", "Contenu": "Content",
    "Sauvegarder le programme de la CPU": "Back up the CPU program",
    "Sauvegarder le programme de l'automate": "Back up the PLC program",
    "Copie de tous les blocs (OB, FB, FC, DB) et de la configuration materielle (SDB).":
      "Copy of all blocks (OB, FB, FC, DB) and of the hardware configuration (SDB).",
    "Copie de l'application de l'automate (programme et configuration), comme le transfert automate -> PC de PL7.":
      "Copy of the PLC application (program and configuration), like the PL7 PLC -> PC transfer.",
    "Sauvegarde deja sur le PC (autre dossier, cle USB, terminal) ou projet STEP 7 V5 (dossier du .s7p, ou archive .zip) :":
      "Backup already on the PC (other folder, USB stick, terminal) or STEP 7 V5 project (.s7p folder, or .zip archive):",
    "Programme deja sur le PC : fichier PL7 .stx, copie .tsxbak (outil terminal) ou .zip de cette page :":
      "Program already on the PC: PL7 .stx file, .tsxbak copy (terminal tool) or .zip from this page:",
    "Importer un .zip ou des blocs .bin": "Import a .zip or .bin blocks", "Importer un dossier": "Import a folder",
    "Importer un .stx, .tsxbak ou .zip": "Import a .stx, .tsxbak or .zip",
    "OB d'erreur vides (banc d'essai sans les modules)": "Empty error OBs (bench test without the modules)",
    "Sans OB122, la CPU passe en STOP au premier acces a un module absent. Ces OB vides la gardent en RUN (le voyant SF reste allume). Seuls les OB absents de la CPU sont charges ; cle de la CPU sur RUN-P ou STOP.":
      "Without OB122 the CPU goes to STOP on the first access to a missing module. These empty OBs keep it in RUN (the SF LED stays on). Only the OBs missing from the CPU are loaded; CPU key switch on RUN-P or STOP.",
    "Charger les OB coches": "Load the ticked OBs",
    "A retirer (ou a valider avec le client) avant de remettre la CPU sur la machine : elle ne s'arretera plus sur un module absent ou une erreur d'execution.":
      "Remove them (or agree with the customer) before putting the CPU back on the machine: it will no longer stop on a missing module or a runtime error.",
    "erreur d'acces a la peripherie": "I/O access error", "erreur d'execution": "runtime error",
    "defaillance de rack / station DP": "rack / DP station failure", "alarme de diagnostic": "diagnostic interrupt",
    "erreur de programmation": "programming error", "erreur de temps (cycle)": "time error (cycle)",
    "erreur de temps (cycle depasse)": "time error (cycle exceeded)", "alarme de diagnostic (module)": "diagnostic interrupt (module)",
    "erreur d'execution (bloc / peripherie absent a la mise a jour)": "runtime error (block / I/O missing at update)",
    "Sauvegardes sur ce PC": "Backups on this PC", "CPU": "CPU", "Blocs": "Blocks",
    "Comparer": "Compare", "Restaurer": "Restore", "Telecharger": "Download", "Supprimer": "Delete",
    "Copie au format .zip (cle USB, e-mail...)": "Copy as .zip (USB stick, e-mail...)",
    "Supprimer cette copie du PC (l'automate n'est pas touche)": "Delete this copy from the PC (the PLC is not touched)",
    "Sauvegarde du programme": "Program backup", "Programme sauvegarde": "Program saved",
    "Aucune sauvegarde : cliquez sur Sauvegarder le programme.": "No backup: click Back up the program.",
    "Aucune copie des donnees : cliquez sur Sauvegarder les donnees de l'automate.": "No data copy: click Back up the PLC data.",
    "sauvegarde du programme": "program backup", "comparaison avec la CPU": "comparison with the CPU",
    "comparaison avec l'automate": "comparison with the PLC",
    "restauration du programme": "program restore", "chargement des OB d'erreur": "loading the error OBs",
    "sauvegarde des donnees": "data backup", "restauration des donnees": "data restore",
    "Programme different de la sauvegarde du": "Program differs from the backup of",
    "bloc(s) different(s) de la sauvegarde du": "block(s) differ from the backup of",
    "Valeurs actuelles differentes (normal en marche, pas un changement de programme) :":
      "Current values differ (normal while running, not a program change):",
    "valeurs actuelles differentes, structure identique": "current values differ, same structure",
    "Blocs de la sauvegarde du": "Blocks of the backup of", "Restaurer la sauvegarde du": "Restore the backup of",
    "Restaurer les donnees du": "Restore the data of", "Sauvegarde du": "Backup of", "Copie du": "Copy of",
    "Charger aussi les DB (remplace les valeurs actuelles de la machine : compteurs, recettes...)":
      "Also load the DBs (replaces the machine's current values: counters, recipes...)",
    "Supprimer de la CPU les blocs absents de la sauvegarde": "Delete from the CPU the blocks missing from the backup",
    "Dans la CPU mais pas dans la sauvegarde :": "In the CPU but not in the backup:",
    "Les DB seront remplacees par celles de la sauvegarde.": "The DBs will be replaced by those of the backup.",
    "La configuration materielle (SDB) n'est pas modifiee.": "The hardware configuration (SDB) is not changed.",
    "La CPU est en RUN : elle sera mise en STOP pendant le chargement.": "The CPU is in RUN: it will be put in STOP during the download.",
    "La CPU est en STOP.": "The CPU is in STOP.", "L'automate est en STOP.": "The PLC is in STOP.",
    "La CPU va passer en STOP : la machine s'arrete.": "The CPU will go to STOP: the machine stops.",
    "L'automate passe en STOP : la machine s'arrete.": "The PLC goes to STOP: the machine stops.",
    "L'automate est en RUN : il sera mis en STOP, la machine s'arrete.": "The PLC is in RUN: it will be put in STOP, the machine stops.",
    "Lancer la restauration": "Start the restore", "Rien a charger": "Nothing to load", "Rien a ecrire": "Nothing to write",
    "Blocs charges :": "Blocks loaded:", "Blocs charges avant l'erreur :": "Blocks loaded before the error:",
    "Blocs supprimes :": "Blocks deleted:", "Chargement arrete :": "Download stopped:", "Restauration arretee :": "Restore stopped:",
    "Restauration terminee : programme identique a la sauvegarde": "Restore finished: program identical to the backup",
    "Restauration terminee, mais la relecture differe": "Restore finished, but the read-back differs",
    "La CPU reste en STOP. Relancer la restauration complete avant de redemarrer.":
      "The CPU stays in STOP. Run the full restore again before restarting.",
    "Redemarrer la CPU (RUN)": "Restart the CPU (RUN)", "Redemarrer la CPU ?": "Restart the CPU?",
    "Redemarrer l'automate (RUN)": "Restart the PLC (RUN)", "Redemarrer l'automate ?": "Restart the PLC?",
    "Relu different :": "Read back different:", "Differences :": "Differences:",
    "A charger dans l'automate :": "To load into the PLC:", "Application faite pour :": "Application made for:",
    "L'application va remplacer le programme de l'automate.": "The application will replace the PLC program.",
    "L'automate passe en STOP, l'application est chargee puis relue pour verification.":
      "The PLC goes to STOP, the application is loaded then read back for checking.",
    "L'automate est vide (pas d'application) : rien a copier avant, l'application sera chargee directement.":
      "The PLC is empty (no application): nothing to copy first, the application will be loaded directly.",
    "Le programme actuel de l'automate est d'abord copie dans la liste (note": "The PLC's current program is first copied into the list (note",
    "Le programme d'avant est garde dans la liste (note": "The previous program is kept in the list (note",
    "L'automate reste en STOP. Le programme d'avant est dans la liste (note": "The PLC stays in STOP. The previous program is in the list (note",
    "ATTENTION : l'application a ete faite pour un": "WARNING: the application was made for a",
    ", l'automate connecte est un": ", the connected PLC is a",
    "Automate different de celui de l'application (": "PLC different from the application's (",
    "Sauvegarde importee :": "Backup imported:", "Donnees importees :": "Data imported:", "Donnees sauvegardees :": "Data saved:",
    "Import de": "Import of", "importe :": "imported:", "Copie prise sur": "Copy taken from",
    "copie importee": "imported copy", "copie de securite": "safety copy", "avant restauration": "before restore",
    "importe depuis des fichiers du PC": "imported from PC files",
    "Aucun fichier .zip, .bin (OB1.bin, FC1.bin...) ou projet STEP 7 dans la selection.":
      "No .zip, .bin file (OB1.bin, FC1.bin...) or STEP 7 project in the selection.",
    "Aucun fichier .stx, .tsxbak ou .zip dans la selection.": "No .stx, .tsxbak or .zip file in the selection.",
    "Aucun fichier .dat ou .csv dans la selection.": "No .dat or .csv file in the selection.",
    "Cocher au moins un OB.": "Tick at least one OB.", "Choisir au moins un OB": "Choose at least one OB",
    "Deja dans la CPU (non remplaces) :": "Already in the CPU (not replaced):",
    "Seuls ceux qui n'existent pas encore sont charges. La CPU ne s'arretera plus sur ces erreurs.":
      "Only those that do not exist yet are loaded. The CPU will no longer stop on these errors.",
    "OB charges :": "OBs loaded:",
    "cle de la CPU sur RUN : la mettre sur RUN-P (ou STOP) pour autoriser le chargement":
      "CPU key switch on RUN: set it to RUN-P (or STOP) to allow downloading",
    "Annule, rien n'a ete ecrit.": "Cancelled, nothing was written.",
    "L'automate est en RUN : le programme peut reecrire certains mots aussitot. Mieux vaut le mettre en STOP avant.":
      "The PLC is in RUN: the program may rewrite some words at once. Better put it in STOP first.",
    "Fonction pas encore disponible pour Delta (programme : capture WPLSoft a faire)":
      "Not available yet for Delta (program: a WPLSoft capture is needed)",
    // watch
    "Adresse": "Address", "Libelle": "Label", "Ajouter": "Add", "Modules en place": "Modules present",
    "Lire le rack pour proposer les entrees / sorties des modules.": "Read the rack to suggest the modules' inputs / outputs.",
    "Lire le rack": "Read the rack",
    "E/I, A/Q, M, DB10.DBW2, T5, Z2. Le format se change sur chaque ligne.": "E/I, A/Q, M, DB10.DBW2, T5, Z2. The format is set on each row.",
    "%MW, %MD, %KW, %KD, %SW, %M, %S (le % est facultatif). Ecriture : %MW et %MD. Le format se change sur chaque ligne.":
      "%MW, %MD, %KW, %KD, %SW, %M, %S (the % is optional). Writing: %MW and %MD. The format is set on each row.",
    "IW0 (mot d'entree), QW0 (mot de sortie), I0 (bit d'entree), Q0 (bit de sortie). Ecriture : QW et Q. Format Pt100 pour le 750-461.":
      "IW0 (input word), QW0 (output word), I0 (input bit), Q0 (output bit). Writing: QW and Q. Pt100 format for the 750-461.",
    "D100, M10, X17 et Y0 (en octal), T5 / C2 (valeur), S0. Ecriture : D, M, Y, T, C, S. Le format se change sur chaque ligne.":
      "D100, M10, X17 and Y0 (octal), T5 / C2 (value), S0. Writing: D, M, Y, T, C, S. The format is set on each row.",
    "Visualiser": "Monitor", "Arreter": "Stop", "Ecrire les valeurs": "Write values", "Exporter": "Export", "Importer": "Import",
    "Vider": "Clear", "Format": "Format", "Valeur": "Value", "Nouvelle valeur": "New value",
    "Visualiser les valeurs (comme les lunettes de TIA Portal / STEP 7)": "Monitor the values (like the glasses in TIA Portal / STEP 7)",
    "Visualisation arretee : cliquer sur les lunettes pour voir les valeurs en direct.": "Monitoring stopped: click the glasses to see live values.",
    "Ajoutez une adresse (ex. MW10) ou importez une table.": "Add an address (e.g. MW10) or import a table.",
    "ex. compteur pieces": "e.g. part counter",
    "Cliquer sur Ajouter pour mettre les voies du module dans la table.": "Click Add to put the module's channels in the table.",
    "Tout ajouter": "Add all", "Aucun module d'entrees / sorties en place.": "No I/O module present.", "Aucun module lu": "No module read",
    "Deja dans la table.": "Already in the table.", "est deja dans la table.": "is already in the table.",
    "Retirer": "Remove", "Retirer toutes les adresses de la table ?": "Remove all addresses from the table?",
    "Tapez une nouvelle valeur dans la colonne de droite, puis Ecrire.": "Type a new value in the right column, then Write.",
    "Ecrire dans l'automate ?": "Write to the PLC?", "Machine en securite ?": "Machine safe?",
    "L'automate peut la refuser.": "The PLC may refuse it.",
    "Automate en RUN : le programme peut reecrire ces valeurs.": "PLC in RUN: the program may rewrite these values.",
    "CPU en RUN : les entrees / sorties sont reecrites a chaque cycle.": "CPU in RUN: the inputs / outputs are rewritten every cycle.",
    "(reecrit par le programme ?)": "(rewritten by the program?)",
    "Format d'affichage": "Display format", "DEC non signe": "unsigned DEC", "pas d'adresse": "no address",
    "# Table de surveillance PLC Online": "# PLC Online watch table",
    "Rien lu": "Nothing read", "valeur illisible": "unreadable value", "valeur erreur": "value error",
    // diagnostic
    "Lecture du tampon de diagnostic, des modules et de la configuration.": "Reading the diagnostic buffer, the modules and the configuration.",
    "Relire": "Read again", "Bits et mots systeme": "System bits and words", "Bits et mots systeme de l'automate.": "System bits and words of the PLC.",
    "Relais et registres speciaux (M1000.., D1000..), entrees / sorties.": "Special relays and registers (M1000.., D1000..), inputs / outputs.",
    "Modules lus dans le coupleur, valeurs de l'image process.": "Modules read from the coupler, process image values.",
    "Signification": "Meaning", "Empl.": "Slot", "Type": "Type", "Adresses": "Addresses",
    "Configure (HW Config)": "Configured (HW Config)", "Configure (PL7)": "Configured (PL7)", "En place": "Present",
    "Tampon de diagnostic": "Diagnostic buffer", "(le plus recent en haut)": "(most recent first)", "ID": "ID",
    "Evenement": "Event", "Classe": "Class", "Parametres CPU": "CPU parameters", "Tampon vide": "Buffer empty",
    "Evenement non repertorie": "Unlisted event", "Image process": "Process image", "Module": "Module", "Valeurs": "Values",
    "Position": "Position", "Configuration materielle non lue.": "Hardware configuration not read.",
    "type non enregistre dans la config chargee": "type not recorded in the loaded config",
    "MODULE DIFFERENT DE LA CONFIGURATION": "MODULE DIFFERENT FROM THE CONFIGURATION",
    "TYPE DIFFERENT DE LA CONFIG, non disponible": "TYPE DIFFERENT FROM THE CONFIG, not available",
    "MODULE ABSENT (rien a cette position)": "MODULE MISSING (nothing in this position)",
    "meme type que configure": "same type as configured", "Module inconnu": "Unknown module",
    "DEFAUT EXTERNE : alimentation capteurs / actionneurs ou voie en defaut": "EXTERNAL FAULT: sensor / actuator supply or faulty channel",
    "position non configuree": "position not configured",
    "Surveillance du cycle": "Cycle monitoring", "par defaut (non modifie dans HW Config)": "default (not changed in HW Config)",
    "Memento de cadence": "Clock memory", "non utilise": "not used", "Remanence": "Retentivity",
    "Alarme cyclique OB35": "Cyclic interrupt OB35", "Non decodes": "Not decoded", "enregistrements": "records",
    "Type :": "Type:", "OB demande": "Requested OB", "Classe de priorite": "Priority class", "Acces": "Access",
    "Adresse accedee": "Address accessed", "Bloc": "Block", "Adresse relative": "Relative address", "Registre en defaut": "Faulty register",
    "erreur externe": "external error", "erreur interne": "internal error",
    "evenement entrant": "incoming event", "evenement sortant": "outgoing event",
    "zone P (peripherie)": "P area (peripheral)", "memoire image des entrees": "process image inputs",
    "memoire image des sorties": "process image outputs",
    "acces bit": "bit access", "acces octet": "byte access", "acces mot": "word access", "acces double mot": "double word access",
    "evenement OB standard": "standard OB event", "erreur synchrone": "synchronous error", "erreur asynchrone": "asynchronous error",
    "changement de mode": "mode change", "evenement de fonctionnement": "operating event", "communication": "communication",
    "systeme H/F": "H/F system", "diagnostic module": "module diagnostics", "evenement utilisateur": "user event",
    "evenement utilisateur libre": "free user event", "module autre que CPU (CP, FM...)": "non-CPU module (CP, FM...)",
    "Etat lu par la requete 4F, decodage a confirmer sur le banc": "Status read with request 4F, decoding to be confirmed on the bench",
    // status
    "Passer en RUN": "Switch to RUN", "Passer en STOP": "Switch to STOP", "Mettre l'horloge a l'heure du PC": "Set the clock to the PC time",
    "Voyants": "LEDs", "Details": "Details", "Defaut groupe": "Group fault", "Forcage actif": "Force active", "Defaut pile": "Battery fault",
    "Defaut bus MPI/DP": "MPI/DP bus fault", "Defaut bus DP": "DP bus fault",
    "Horloge CPU": "CPU clock", "Horloge automate": "PLC clock", "Horloge": "Clock", "Cle": "Key switch", "Protection": "Protection",
    "Somme programme": "Program checksum", "Somme config materielle": "Hardware config checksum",
    "Version materiel": "Hardware version", "N° de serie": "Serial number", "N de serie": "Serial number",
    "(sans mot de passe)": "(no password)", "(decalee)": "(wrong)", "(en defaut)": "(faulty)",
    "(eteint)": "(off)", "(allume)": "(on)", "(clignote)": "(flashing)",
    "Defaut E/S": "I/O fault", "Chien de garde (%S11)": "Watchdog (%S11)", "Pile (%S68)": "Battery (%S68)",
    "Chien de garde": "Watchdog", "Cycle (dernier / max / min)": "Cycle (last / max / min)",
    "Sauvegarde Flash du programme": "Program Flash backup", "%MW sauvegardes en Flash": "%MW saved in Flash",
    "Derniere sauvegarde Flash": "Last Flash backup", "aucune": "none", "aucun": "none",
    "Etat CPU (requete 4F)": "CPU status (request 4F)", "Etat CPU (4F)": "CPU status (4F)", "Application": "Application",
    "Defaut E/S signale par la CPU (voyant I/O) : voir l'onglet Diagnostic": "I/O fault reported by the CPU (I/O LED): see the Diagnostics tab",
    "Pas d'application dans l'automate (vide : programme efface ou chargement refuse)":
      "No application in the PLC (empty: program erased or download refused)",
    "Defauts des modules non lus (requete refusee)": "Module faults not read (request refused)",
    "demarrage a froid": "cold start", "reprise a chaud": "warm restart", "defaut E/S": "I/O fault",
    "debordement chien de garde": "watchdog overflow", "premier cycle apres RUN": "first cycle after RUN",
    "defaut E/S dans la tache": "I/O fault in the task", "depassement periode de tache": "task period overrun",
    "horloge temps reel perdue (heure fausse)": "real-time clock lost (wrong time)", "pile carte memoire": "memory card battery",
    "pile processeur": "processor battery", "chien de garde (ms)": "watchdog (ms)", "duree du dernier cycle (ms)": "last cycle time (ms)",
    "cycle max (ms)": "max cycle (ms)", "cycle min (ms)": "min cycle (ms)", "sauvegarde Flash (%SW96)": "Flash backup (%SW96)",
    "%MW en Flash (%SW97)": "%MW in Flash (%SW97)",
    "entrees / sorties TOR": "digital inputs / outputs", "entrees TOR": "digital inputs", "sorties TOR": "digital outputs",
    "entrees analogiques": "analog inputs", "sorties analogiques": "analog outputs",
    "Entrees TOR": "Digital inputs", "Sorties TOR": "Digital outputs",
    "Entrees analogiques / complexes": "Analog / complex inputs", "Sorties analogiques / complexes": "Analog / complex outputs",
    "Image entrees": "Input image", "Image sorties": "Output image", "Modules": "Modules", "Adresse IP": "IP address",
    "coupleur": "coupler", "coupleur Modbus TCP": "Modbus TCP coupler", "module de fin": "end module",
    "alimentation 24 V": "24 V supply", "module passif": "passive module", "module special": "special module",
    "Dernier code I/O (0x1020)": "Last I/O code (0x1020)", "Dernier code I/O": "Last I/O code",
    "Code d'erreur du voyant I/O (0x1020) non lu": "I/O LED error code (0x1020) not read",
    "rupture de fil ou hors plage haute (LED rouge)": "broken wire or above range (red LED)",
    "court-circuit ou sous -200 °C (LED rouge)": "short circuit or below -200 °C (red LED)",
    "(rupture fil / hors plage)": "(broken wire / out of range)", "court-circuit / < -200 °C": "short circuit / < -200 °C",
    "Un module de taille inconnue : adresses des modules suivants non calculees":
      "A module of unknown size: addresses of the following modules not calculated",
    "le coupleur et le PC doivent etre dans le meme reseau (ex. 192.168.1.10 et 192.168.1.200)":
      "the coupler and the PC must be in the same network (e.g. 192.168.1.10 and 192.168.1.200)",
    "Adresse IP du coupleur inconnue : Recherche automatique d'abord": "Coupler IP address unknown: run Automatic search first",
    "Fonction non disponible pour un coupleur WAGO": "Not available for a WAGO coupler",
    "erreur programme / execution": "program / runtime error", "tension basse": "low voltage",
    "Capacite programme": "Program capacity", "Capacite": "Capacity", "Cycle": "Cycle",
    "Entrees integrees": "Built-in inputs", "Sorties integrees": "Built-in outputs",
    "Entrees d'extension": "Expansion inputs", "Sorties d'extension": "Expansion outputs",
    "RUN (contact toujours ON en RUN)": "RUN (contact always ON in RUN)",
    "erreur de syntaxe du programme (code dans D1004)": "program syntax error (code in D1004)",
    "chien de garde depasse (WDT, pas dans D1008)": "watchdog exceeded (WDT, step in D1008)",
    "tension 24 V insuffisante (LV)": "24 V supply too low (LV)",
    "erreur d'execution (code dans D1067)": "runtime error (code in D1067)",
    "erreur d'execution verrouillee (pas dans D1068)": "latched runtime error (step in D1068)",
    "modules speciaux a droite": "special modules on the right", "entrees X d'extension": "expansion X inputs",
    "sorties Y d'extension": "expansion Y outputs", "version firmware": "firmware version",
    "capacite programme (pas)": "program capacity (steps)", "code d'erreur de syntaxe": "syntax error code",
    "pas du depassement WDT": "step of the WDT overrun", "cycle actuel (0,1 ms)": "current cycle (0.1 ms)",
    "cycle mini (0,1 ms)": "min cycle (0.1 ms)", "cycle maxi (0,1 ms)": "max cycle (0.1 ms)",
    "code d'erreur d'execution": "runtime error code", "pas de l'erreur d'execution": "step of the runtime error",
    "pas de l'erreur d'operande": "step of the operand error", "pas d'instruction END dans le programme": "no END instruction in the program",
    "erreur de conversion BCD": "BCD conversion error", "division par zero": "division by zero",
    "operande hors plage (adresse indexee E / F trop grande ?)": "operand out of range (indexed E / F address too large?)",
    "racine carree d'un nombre negatif": "square root of a negative number",
    "erreur FROM / TO : le module special adresse ne repond pas (absent, en defaut, ou numero de module faux)":
      "FROM / TO error: the addressed special module does not answer (missing, faulty, or wrong module number)",
    "voir la liste des codes du manuel DVP": "see the code list in the DVP manual",
    "erreur de syntaxe / structure du programme": "program syntax / structure error", "operande hors plage dans le programme": "operand out of range in the program",
    "Tension 24 V insuffisante (M1009) : verifier l'alimentation": "24 V supply too low (M1009): check the power supply",
    "(non conserve : defaut au redemarrage)": "(not kept: default at restart)",
    "erreur materielle ou de configuration du coupleur": "coupler hardware or configuration error",
    "erreur de configuration du bus interne (modules)": "internal bus configuration error (modules)",
    "erreur de protocole du bus interne": "internal bus protocol error",
    "erreur physique du bus interne : module defectueux ou module de fin absent": "internal bus physical error: faulty module or end module missing",
    "erreur d'initialisation du bus interne": "internal bus initialisation error",
    "erreur de configuration du bus de terrain (adresse IP...)": "fieldbus configuration error (IP address...)",
    "module non supporte": "unsupported module", "module non supporte / diagnostic": "unsupported module / diagnostics",
    "erreur de programme (coupleur programmable)": "program error (programmable coupler)",
    "MAC ID invalide (remplacer le coupleur)": "invalid MAC ID (replace the coupler)",
    "erreur d'initialisation du materiel Ethernet": "Ethernet hardware initialisation error",
    "erreur d'initialisation TCP/IP": "TCP/IP initialisation error",
    "pas d'adresse IP (configuration reseau / serveur BootP)": "no IP address (network configuration / BootP server)",
    "erreur d'initialisation du protocole applicatif": "application protocol initialisation error",
    "image process trop grande (trop de modules)": "process image too large (too many modules)",
    "adresse IP en double sur le reseau": "duplicate IP address on the network",
    "erreur de construction de l'image process": "process image build error",
    "erreur de correspondance modules / bus de terrain : verifier le fichier EA-config.xml du coupleur":
      "module / fieldbus mapping error: check the coupler's EA-config.xml file",
    "erreur bus de terrain": "fieldbus error", "code inconnu": "unknown code", "Modbus TCP (pas WAGO ?)": "Modbus TCP (not WAGO?)",
    "non lu": "not read", "non lue": "not read", "non disponible": "not available", "format non reconnu": "format not recognised",
    "terminee sans erreur": "finished without error", "pas encore": "not yet",
    // CPU actions
    "Passer la CPU en STOP ? La machine s'arrete, les sorties sont coupees.": "Put the CPU in STOP? The machine stops, the outputs are switched off.",
    "Passer la CPU en RUN ? La machine peut demarrer.": "Put the CPU in RUN? The machine may start.",
    "La machine repart.": "The machine restarts.",
    "Arret demande (STOP).": "Stop requested (STOP).", "Demarrage demande (RUN).": "Start requested (RUN).",
    "Demarrage demande — verifier le voyant RUN": "Start requested — check the RUN LED",
    "Demarrage demande, mais la cle est sur STOP : la CPU restera en STOP (mettre la cle sur RUN ou RUN-P).":
      "Start requested, but the key switch is on STOP: the CPU will stay in STOP (set it to RUN or RUN-P).",
    "Passer la CPU de STOP a RUN si elle est arretee.": "Switch the CPU from STOP to RUN if it is stopped.",
    "Mettre l'horloge de la CPU a l'heure de ce PC (": "Set the CPU clock to this PC's time (",
    "Horloge CPU reglee :": "CPU clock set:",
    // WAGO search
    "Adresse a donner au coupleur": "Address to give the coupler", "Donner cette adresse": "Give this address",
    "Appareils trouves : choisir.": "Devices found: choose.",
    "Adresse en cours d'envoi (fenetre administrateur possible, jusqu'a 60 s)...": "Sending the address (administrator prompt possible, up to 60 s)...",
    "Balayage de 9 reseaux courants (1 minute environ)...": "Sweeping 9 common networks (about 1 minute)...",
    "Changement de l'adresse du PC (fenetre administrateur)...": "Changing the PC address (administrator prompt)...",
    "Retour en automatique (fenetre administrateur)...": "Back to automatic (administrator prompt)...",
    "Recherche en cours (35 s) : ETEINDRE PUIS RALLUMER LE COUPLEUR MAINTENANT (il demande son adresse au demarrage)...":
      "Searching (35 s): SWITCH THE COUPLER OFF AND ON NOW (it asks for its address at startup)...",
    "Le PC prendra .200 dans le meme reseau.": "The PC will take .200 in the same network.",
    "Le PC va prendre une adresse dans chaque reseau courant le temps du balayage": "The PC will take an address in each common network during the sweep",
    "(fenetre administrateur Windows). Continuer ?": "(Windows administrator prompt). Continue?",
    "Carte WAGO vue dans la table ARP :": "WAGO card seen in the ARP table:",
    "Le coupleur n'a pas d'adresse IP et en demande une (mode DHCP / BootP) : choisir l'adresse a lui donner.":
      "The coupler has no IP address and is asking for one (DHCP / BootP mode): choose the address to give it.",
    "Le coupleur a donc probablement une adresse fixe dans un autre reseau : Balayer les reseaux courants.":
      "So the coupler probably has a fixed address in another network: Sweep common networks.",
    "Mettre les micro-interrupteurs d'adresse du coupleur sur une valeur (ex. 10) et le redemarrer.":
      "Set the coupler's address DIP switches to a value (e.g. 10) and restart it.",
    "Ou : eteindre / rallumer le coupleur et relancer (il demande une adresse au demarrage).":
      "Or: switch the coupler off / on and search again (it asks for an address at startup).",
    "Pare-feu Windows : autoriser Python (reseaux prives ET publics), sinon la demande d'adresse n'arrive pas.":
      "Windows firewall: allow Python (private AND public networks), otherwise the address request does not get through.",
    "Rien trouve sur les reseaux du PC (": "Nothing found on the PC's networks (", "Rien dans les reseaux courants (": "Nothing in the common networks (",
    ") et aucune demande d'adresse entendue.": ") and no address request heard.",
    "qui demande une adresse (": "asking for an address (", "Ecoute en partie impossible (": "Listening partly impossible (",
    "Le coupleur n'a pas redemande d'adresse en 60 s : l'eteindre / rallumer pendant l'attente et recommencer.":
      "The coupler did not ask for an address again within 60 s: switch it off / on while waiting and try again.",
    "Pare-feu Windows : autoriser Python.": "Windows firewall: allow Python.",
    "Recherche impossible :": "Search impossible:", "Serveur d'adresse impossible :": "Address server impossible:",
    "changement refuse (fenetre administrateur annulee ?)": "change refused (administrator prompt cancelled?)",
    "changement d'adresse IP : seulement sous Windows": "IP address change: Windows only",
    "Reseau :": "Network:",
    // generic server errors
    "Serveur injoignable :": "Server unreachable:", "Reponse illisible du serveur": "Unreadable server reply",
    "Erreur": "Error", "Erreur interne :": "Internal error:", "Refuse par l'automate :": "Refused by the PLC:", "Refuse :": "Refused:",
    "Operation en cours": "Operation in progress", "Marque non disponible :": "Brand not available:",
    "Sauvegarde inconnue": "Unknown backup", "Sauvegarde introuvable": "Backup not found", "Fichiers trop gros": "Files too large",
    "Fichiers trop gros pour une copie de programme S7-300": "Files too large for an S7-300 program copy",
    "Aucun bloc S7 (OB1.bin, FC1.bin...) ni projet STEP 7 dans les fichiers choisis": "No S7 block (OB1.bin, FC1.bin...) or STEP 7 project in the chosen files",
    "Aucun bloc dans la CPU": "No block in the CPU", "numeros d'OB invalides": "invalid OB numbers",
    "OB d'erreur : S7-300 seulement": "Error OBs: S7-300 only",
    "Sauvegarde des donnees : TSX et Delta seulement pour l'instant": "Data backup: TSX and Delta only for now",
    "Adresse MPI, rack et emplacement : des nombres": "MPI address, rack and slot: numbers",
    "Vitesse et station : des nombres": "Baud rate and station: numbers",
    "lecture refusee par la CPU (adresse hors de la memoire ?)": "read refused by the CPU (address outside memory?)",
    "lecture refusee par l'automate (adresse hors de la zone configuree ?)": "read refused by the PLC (address outside the configured area?)",
    "ecriture refusee (demo)": "write refused (demo)", "adresse hors de la memoire (demo)": "address outside memory (demo)",
    "absent de la demo": "missing from the demo",
    "Identification non lue :": "Identification not read:", "Cle et protection non lues :": "Key switch and protection not read:",
    "Tampon de diagnostic non lu :": "Diagnostic buffer not read:", "Modules en place non lus :": "Modules present not read:",
    "Configuration materielle non lue :": "Hardware configuration not read:", "Parametres CPU non lus :": "CPU parameters not read:",
    "Configuration non lue :": "Configuration not read:", "Modules non lus :": "Modules not read:",
    "Projet STEP 7 illisible :": "Unreadable STEP 7 project:", "fichier zip illisible": "unreadable zip file", "trop gros": "too large",
    "n'est pas un bloc S7 valide": "is not a valid S7 block", "aucun bloc compile (OB, FB, FC, DB)": "no compiled block (OB, FB, FC, DB)",
    "Aucun programme TSX (.stx de PL7, .tsxbak ou .zip) dans les fichiers choisis": "No TSX program (PL7 .stx, .tsxbak or .zip) in the chosen files",
    "Aucun fichier de donnees (.dat de PL7 ou .csv) dans les fichiers choisis": "No data file (PL7 .dat or .csv) in the chosen files",
    "Aucun fichier .csv de donnees Delta dans les fichiers choisis": "No Delta data .csv file in the chosen files",
    "l'automate n'a pas d'application (rien a copier)": "the PLC has no application (nothing to copy)",
    "pyserial manque : pip install pyserial": "pyserial missing: pip install pyserial",
    "Choisir le bon dans la liste.": "Choose the right one in the list.",
    "PL7 ferme ? Cable TSX PCX 1031 branche sur la prise TER, meme position du selecteur qu'avec PL7 ?":
      "PL7 closed? TSX PCX 1031 cable on the TER port, selector in the same position as with PL7?",
    "L'automate ne repond pas sur": "The PLC does not answer on",
    "Fils + / - inverses ? COM2 utilise par le programme ? Essayer python tools/delta.py chercher --port":
      "+ / - wires swapped? COM2 used by the program? Try python tools/delta.py chercher --port",
    "Automate reserve par un autre poste (PL7 connecte ?) : configuration prise dans la":
      "PLC reserved by another station (PL7 connected?): configuration taken from the",
    "Fermer PL7 (ou le deconnecter), ou eteindre / rallumer l'automate pour liberer la reservation, puis Relire.":
      "Close PL7 (or disconnect it), or switch the PLC off / on to release the reservation, then Read again.",
    "Le PC est-il dans le meme reseau (meme debut d'adresse, ex. 192.168.1.x) ? Bouton Chercher.":
      "Is the PC in the same network (same address start, e.g. 192.168.1.x)? Search button.",
    "Pas de reponse de": "No answer from", "sur le port 502": "on port 502",
    "mais le port 502 ne repond pas encore : attendre quelques secondes puis Connecter.":
      "but port 502 does not answer yet: wait a few seconds then Connect.",
    "lecture des %I / %Q pas encore possible sur TSX : lancer une fois python tools/tsx.py chercher-es (voir la doc)":
      "reading %I / %Q not possible yet on TSX: run python tools/tsx.py chercher-es once (see the docs)",
    "adresse TSX non reconnue (ex. %MW10, %MD4, %KW0, %SW30, %M5, %S10)": "TSX address not recognised (e.g. %MW10, %MD4, %KW0, %SW30, %M5, %S10)",
    "adresse Delta : D100, M10, X17 (octal), Y0, T5, C2, S0": "Delta address: D100, M10, X17 (octal), Y0, T5, C2, S0",
    "adresse WAGO : IW0 (mot d'entree), QW0 (mot de sortie), I0 (bit d'entree), Q0 (bit de sortie)":
      "WAGO address: IW0 (input word), QW0 (output word), I0 (input bit), Q0 (output bit)",
    "une entree ne s'ecrit pas : seulement QW / Q": "an input cannot be written: only QW / Q",
    "les entrees X ne s'ecrivent pas": "X inputs cannot be written", "X et Y sont en octal (pas de chiffre 8 ou 9)": "X and Y are octal (no digit 8 or 9)",
    "n'est pas un mot": "is not a word", "hors plage": "out of range", "aucun mot D dans le fichier": "no D word in the file",
    "ligne illisible :": "unreadable line:", "copie illisible :": "unreadable copy:",
    "valeur illisible pour le format": "unreadable value for format", "hors de l'image du coupleur": "outside the coupler image",
    "adresse invalide :": "invalid address:", "ecriture non autorisee sur": "writing not allowed on",
    "ecriture possible sur %MW et %MD seulement": "writing possible on %MW and %MD only",
    "liste des modules (0x2030) refusee par le coupleur": "module list (0x2030) refused by the coupler",
    "pas de reponse (timeout)": "no answer (timeout)", "Requete illisible": "Unreadable request", "Requete trop grosse": "Request too large",
    "Action inconnue": "Unknown action", "Page introuvable": "Page not found",
    "pilote S7ONLINE non ouvert : STEP 7 est-il encore ouvert ? S7onlinx.dll est-il installe ?":
      "S7ONLINE driver not opened: is STEP 7 still open? Is S7onlinx.dll installed?",
    "Le pilote S7ONLINE n'existe que sous Windows.": "The S7ONLINE driver exists only on Windows.",
    "echec initialisation adaptateur :": "adapter initialisation failed:", "pas de CPU a l'adresse MPI": "no CPU at MPI address",
    "passage en STOP refuse :": "switch to STOP refused:", "demarrage refuse :": "start refused:",
    "identification de la CPU non lue (SZL 0111 / 011C)": "CPU identification not read (SZL 0111 / 011C)",
    "la mise a l'heure n'a pas pris": "the clock setting did not take",
    "verification (relecture du programme)": "checking (reading the program back)",
    // TSX module descriptions
    "32 entrees 24 V cc (connecteurs HE10)": "32 inputs 24 V DC (HE10 connectors)", "12 entrees 24 V cc": "12 inputs 24 V DC",
    "8 entrees 120 V ca": "8 inputs 120 V AC", "8 entrees 220 V ca": "8 inputs 220 V AC", "32 sorties relais": "32 relay outputs",
    "32 sorties transistors 24 V cc": "32 transistor outputs 24 V DC", "8 sorties relais": "8 relay outputs",
    "8 sorties transistors 24 V cc": "8 transistor outputs 24 V DC", "4 sorties transistors 24 V cc 2 A": "4 transistor outputs 24 V DC 2 A",
    "16 entrees 24 V cc + 12 sorties relais": "16 inputs 24 V DC + 12 relay outputs",
    "16 entrees 24 V cc + 12 sorties transistors": "16 inputs 24 V DC + 12 transistor outputs",
    "16 entrees + 12 sorties transistors (HE10)": "16 inputs + 12 transistor outputs (HE10)",
    "32 entrees + 32 sorties transistors (HE10)": "32 inputs + 32 transistor outputs (HE10)",
    "4 entrees analogiques (thermocouple, sonde, +/-10 V)": "4 analog inputs (thermocouple, probe, +/-10 V)",
    "8 entrees analogiques tension": "8 analog voltage inputs", "8 entrees analogiques courant": "8 analog current inputs",
    "4 sorties analogiques +/-10 V": "4 analog outputs +/-10 V", "2 sorties analogiques": "2 analog outputs",
    "1 voie de comptage": "1 counting channel", "2 voies de comptage": "2 counting channels",
    // Panasonic FP0R
    "Passer l'automate en PROG ? La machine s'arrete, les sorties sont coupees.": "Put the PLC in PROG? The machine stops, the outputs are switched off.",
    "Arret demande (PROG).": "Stop requested (PROG).",
    "Chercher les reglages": "Find the settings", "Visualisation en cours": "Monitoring", "libelle": "label",
    "registre special, rempli par l'automate (lecture seule). Le cycle maxi DT90024 repart de zero au passage PROG -> RUN.":
      "special register, filled in by the PLC (read only). The max cycle DT90024 restarts from zero on PROG -> RUN.",
    "BCC faux": "wrong BCC", "format de commande": "command format", "commande non supportee": "command not supported",
    "trames multiples": "multiple frames", "parametre invalide": "invalid parameter", "donnee invalide": "invalid data",
    "enregistrement (trop de contacts / registres)": "registration (too many contacts / registers)",
    "donnee manquante": "missing data",
    "Bits de stop (1 ou 2)": "Stop bits (1 or 2)", "2 bits de stop.": "2 stop bits.",
    "Bits de donnees : 7 ou 8, bits de stop : 1 ou 2": "Data bits: 7 or 8, stop bits: 1 or 2", "Bits de donnees (7 ou 8)": "Data bits (7 or 8)",
    "Bits de donnees : 7 ou 8": "Data bits: 7 or 8", "Vitesse, bits et station : des nombres": "Baud rate, bits and station: numbers",
    "Reglages remplis : cliquer sur Connecter.": "Settings filled in: click Connect.",
    "Deconnecter d'abord : le port COM est utilise par la connexion en cours": "Disconnect first: the COM port is used by the current connection",
    "Port COM a renseigner": "COM port to fill in",
    "Cable sur le port outil (mini-DIN 5) ? FPWIN ferme ? Reglages du port changes : bouton Chercher les reglages.":
      "Cable on the tool port (mini-DIN 5)? FPWIN closed? Port settings changed: Find the settings button.",
    "Seulement l'echo de nos trames : le cable renvoie ce qu'il envoie, l'automate ne repond pas. Cable branche sur le port outil ? Automate sous tension ?":
      "Only the echo of our frames: the cable sends back what it sends, the PLC does not answer. Cable on the tool port? PLC powered?",
    "Silence total sur toutes les vitesses. Mauvais port COM (voir le Gestionnaire de peripheriques), cable debranche, ou port outil en mode general (utilise par le programme : F159 MREC / F144 TRNS). Avec FPWIN : Hors ligne -> Registres systeme -> Port outil.":
      "Total silence at every speed. Wrong COM port (see Device Manager), cable unplugged, or tool port in general-purpose mode (used by the program: F159 MREC / F144 TRNS). In FPWIN: Offline -> System registers -> Tool port.",
    "pas en MEWTOCOL : il a ete regle en Modbus dans les registres systeme (412..). Le remettre en MEWTOCOL (liaison ordinateur) avec FPWIN, ou passer par le port USB de l'automate s'il en a un.":
      "not MEWTOCOL: it was set to Modbus in the system registers (412..). Set it back to MEWTOCOL (computer link) with FPWIN, or use the PLC's USB port if it has one.",
    "CPU (E/S integrees)": "CPU (built-in I/O)",
    "E/S par extension (8 = FP0-E16, 16 = FP0-E32, 4 = FP0-E8R)": "I/O per expansion (8 = FP0-E16, 16 = FP0-E32, 4 = FP0-E8R)", "module 32 points ou analogique": "32-point or analog module", "Entrees / sorties integrees": "Built-in inputs / outputs",
    "le FP0R ne donne pas le type du module : valeurs lues a ses adresses": "the FP0R does not report the module type: values read at its addresses",
    "tout a 0 (module absent, ou entrees / sorties a 0)": "all 0 (no module, or inputs / outputs at 0)",
    "Demo (Panasonic FP0R simule)": "Demo (simulated Panasonic FP0R)", "Passer en PROG": "Switch to PROG",
    "Panasonic : cable USB / RS-232 sur le port outil (mini-DIN 5),": "Panasonic: USB / RS-232 cable on the tool port (mini-DIN 5),",
    "FPWIN ferme": "FPWIN closed",
    ". Defaut du port outil : 9600 bauds, 8 bits, parite impaire (O), station 1. RUN / PROG a distance : selecteur de l'automate sur RUN.":
      ". Tool port default: 9600 baud, 8 bits, odd parity (O), station 1. Remote RUN / PROG: PLC mode switch on RUN.",
    "DT100, WX0 / WY0 / WR10 (mots), X0, X1F, Y3, R901C (bits : mot decimal + bit hexa). Ecriture : DT, WY, WR, Y, R. Format dint pour les compteurs 32 bits.":
      "DT100, WX0 / WY0 / WR10 (words), X0, X1F, Y3, R901C (bits: decimal word + hex bit). Writing: DT, WY, WR, Y, R. dint format for 32-bit counters.",
    "Copie de tous les registres DT (recettes, compteurs, reglages), sans le programme. Environ 1 a 2 minutes a 9600 bauds.":
      "Copy of all DT registers (recipes, counters, settings), without the program. About 1 to 2 minutes at 9600 baud.",
    "Relais et registres speciaux (R9000.., DT90000..), entrees / sorties integrees.": "Special relays and registers (R9000.., DT90000..), built-in inputs / outputs.",
    "erreur d'autodiagnostic (code dans DT90000)": "self-diagnostic error (code in DT90000)",
    "erreur d'operation, memorisee (adresse dans DT90017)": "operation error, latched (address in DT90017)",
    "erreur d'operation, derniere (adresse dans DT90018)": "operation error, latest (address in DT90018)",
    "erreur de communication port outil / RS-232C": "tool port / RS-232C communication error",
    "erreur de cycle constant": "constant scan error", "toujours ON": "always ON",
    "impulsion au demarrage (1er cycle)": "initial pulse (first scan)", "horloge 1 s": "1 s clock",
    "mode RUN": "RUN mode", "message (F149 MSG)": "message (F149 MSG)", "mode commande a distance": "remote mode",
    "forcage en cours": "forcing active", "code d'autodiagnostic (E..)": "self-diagnostic code (E..)",
    "adresse (pas) de l'erreur d'operation, memorisee": "operation error address (step), latched",
    "adresse (pas) de l'erreur d'operation, derniere": "operation error address (step), latest",
    "cycle actuel (0,1 ms)": "current scan (0.1 ms)",
    "Autodiagnostic": "Self-diagnostics", "Commande a distance": "Remote mode", "oui": "yes", "non": "no",
    "erreur d'autodiagnostic / d'operation": "self-diagnostic / operation error",
    "Code modele (RT)": "Model code (RT)", "Version": "Version", "Reponse RT brute": "Raw RT reply", "Decodage RT": "RT decoding",
    "a confirmer sur un FP0R (le mode affiche vient de R9020)": "to be confirmed on an FP0R (the mode shown comes from R9020)",
    "Etat RT": "RT status", "Cycle constant depasse (R900F)": "Constant scan exceeded (R900F)",
    "Forcage en cours (R9029) : des entrees / sorties sont forcees": "Forcing active (R9029): some inputs / outputs are forced",
    "erreur de syntaxe": "syntax error", "sortie en double": "duplicate output",
    "instructions non appariees (MC/MCE, JP/LBL...)": "unpaired instructions (MC/MCE, JP/LBL...)",
    "parametres incoherents (registres systeme)": "parameter mismatch (system registers)",
    "zone de programme (instruction hors zone)": "program area error (instruction outside the area)",
    "memoire de compilation pleine": "compile memory full", "type d'instruction evoluee": "high-level instruction type error",
    "operandes d'instruction evoluee": "high-level instruction operand error",
    "erreur memoire programme (ROM utilisateur)": "program memory error (user ROM)",
    "trop de modules d'extension": "too many expansion units", "trop de modules enregistres": "too many registered units",
    "erreur d'operation (arret sur erreur d'operation)": "operation error (stop on operation error)",
    "erreur pile de sauvegarde": "backup battery error",
    "erreur definie par le programme (F148 ERR)": "error set by the program (F148 ERR)",
    "voir la liste des codes du manuel FP0R": "see the code list in the FP0R manual",
    "adresse FP0R : DT100, WX0, WY0, WR10, X0, X1F, Y3, R901C": "FP0R address: DT100, WX0, WY0, WR10, X0, X1F, Y3, R901C",
    "format dint : DT seulement": "dint format: DT only", "hors limites 32 bits": "outside 32-bit limits",
    "aucun mot DT dans le fichier": "no DT word in the file",
    "Aucun fichier .csv de donnees FP0R dans les fichiers choisis": "No FP0R data .csv file in the chosen files",
    "Sauvegarde des donnees : TSX, Delta et Panasonic seulement pour l'instant": "Data backup: TSX, Delta and Panasonic only for now",
    "Fonction pas encore disponible pour le FP0R (programme : python tools/panasonic.py programme)":
      "Not available yet for the FP0R (program: python tools/panasonic.py programme)",
    "Cable sur le port outil (mini-DIN 5) ? FPWIN ferme ? Essayer 115200 ou la parite O / N (reglage du port outil dans les registres systeme).":
      "Cable on the tool port (mini-DIN 5)? FPWIN closed? Try 115200 or parity O / N (tool port setting in the system registers).",
    "l'automate a refuse la commande": "the PLC refused the command",
    "mode de l'automate (commande impossible dans ce mode : selecteur RUN / PROG ?)": "PLC mode (command not possible in this mode: RUN / PROG switch?)",
    "protection (mot de passe ou memoire protegee)": "protection (password or protected memory)", "adresse hors plage": "address out of range",
    "Erreur d'autodiagnostic (R9000) : DT90000 =": "Self-diagnostic error (R9000): DT90000 =",
    // Allen-Bradley Logix / Kinetix
    "Allen-Bradley ControlLogix / GuardLogix (Ethernet)": "Allen-Bradley ControlLogix / GuardLogix (Ethernet)",
    "Demo (GuardLogix 5580 + Kinetix 5500 simules)": "Demo (simulated GuardLogix 5580 + Kinetix 5500)",
    "Adresse IP de l'automate": "PLC IP address", "non lus": "not read", "Axes": "Axes", "Axe": "Axis", "Statut": "Status",
    "Allen-Bradley : cable Ethernet sur le reseau de l'automate (port de la CPU ou module 1756-EN2T), PC dans le meme reseau. Emplacement CPU : 0 en general. Necessite":
      "Allen-Bradley: Ethernet cable on the PLC network (CPU port or 1756-EN2T module), PC in the same network. CPU slot: usually 0. Needs",
    "Axes Kinetix lus dans l'automate (tags AXIS_CIP_DRIVE), toutes les secondes. Lecture seule : le reset des defauts se fait par le programme ou Studio 5000. Codes FLT / INHIBIT identiques a l'afficheur du variateur.":
      "Kinetix axes read from the controller (AXIS_CIP_DRIVE tags), every second. Read only: faults are reset by the program or Studio 5000. FLT / INHIBIT codes as on the drive display.",
    "Defauts / inhibitions": "Faults / inhibits", "Bus CC": "DC bus", "Charge moteur / variateur": "Motor / drive load",
    "Position / vitesse": "Position / velocity", "Historique": "History",
    "(depuis l'ouverture de l'onglet, le plus recent en haut)": "(since the tab was opened, most recent first)",
    "Heure": "Time", "Aucun axe AXIS_CIP_DRIVE dans l'automate.": "No AXIS_CIP_DRIVE axis in the controller.",
    "Aucun changement depuis l'ouverture de l'onglet.": "No change since the tab was opened.",
    "Initialisation": "Initializing", "Pre-charge (bus CC en charge)": "Pre-charge (DC bus charging)", "Arrete": "Stopped",
    "Demarrage": "Starting", "En marche": "Running", "Test": "Testing", "Arret en cours": "Stopping",
    "Arret d'urgence": "Aborting", "DEFAUT MAJEUR": "MAJOR FAULT", "Demarrage inhibe": "Start inhibited",
    "Arret (shutdown)": "Shutdown", "DEFAUT": "FAULT",
    "defaut physique de l'axe (variateur / moteur / retour)": "physical axis fault (drive / motor / feedback)",
    "defaut module (connexion variateur)": "module fault (drive connection)", "defaut de configuration": "configuration fault",
    "defaut de groupe d'axes": "motion group fault", "defaut de mouvement": "motion fault",
    "defaut de garde (STO cable)": "guard fault (hardwired STO)", "defaut d'initialisation": "initialization fault",
    "defaut APR (position absolue)": "APR fault (absolute position)", "defaut de securite (STO integre)": "safety fault (integrated STO)",
    "survitesse moteur (limite usine)": "motor overspeed (factory limit)", "survitesse moteur (limite utilisateur)": "motor overspeed (user limit)",
    "surcharge moteur (limite usine)": "motor overload (factory limit)", "surcharge moteur (limite utilisateur)": "motor overload (user limit)",
    "surintensite onduleur": "inverter overcurrent", "surcharge onduleur (limite usine)": "inverter overload (factory limit)",
    "surcharge onduleur (limite utilisateur)": "inverter overload (user limit)", "courant de fuite a la terre": "ground current",
    "perte de phase reseau": "AC phase loss", "echec de pre-charge": "pre-charge failure",
    "bruit sur le retour (limite usine)": "feedback signal noise (factory limit)", "bruit sur le retour (limite utilisateur)": "feedback signal noise (user limit)",
    "perte du signal de retour (limite usine)": "feedback signal loss (factory limit)", "perte du signal de retour (limite utilisateur)": "feedback signal loss (user limit)",
    "communication codeur (limite usine)": "feedback communication (factory limit)", "erreur de position excessive": "excessive position error",
    "erreur de vitesse excessive": "excessive velocity error", "limite de surcouple": "overtorque limit", "limite de sous-couple": "undertorque limit",
    "entree de validation desactivee": "enable input deactivated", "exception demandee par l'automate": "controller initiated exception",
    "(voir le tableau des codes du manuel 2198-UM001)": "(see the fault code table in manual 2198-UM001)",
    "(defaut propre au variateur, voir le manuel 2198-UM001)": "(drive-specific fault, see manual 2198-UM001)",
    "(defaut d'initialisation : configuration / moteur / firmware)": "(initialization fault: configuration / motor / firmware)",
    "(connexion avec le variateur)": "(connection with the drive)",
    "entree de validation (enable) absente": "enable input missing",
    "(propre au variateur : Safe Torque Off le plus souvent)": "(drive-specific: usually Safe Torque Off)",
    "defaut mineur recuperable": "minor recoverable fault", "defaut mineur non recuperable": "minor unrecoverable fault",
    "DEFAUT MAJEUR recuperable": "MAJOR recoverable fault", "DEFAUT MAJEUR non recuperable": "MAJOR unrecoverable fault",
    "defaut mineur": "minor fault", "defaut majeur": "major fault",
    "auto-test / inconnu": "self-test / unknown", "mise a jour firmware": "firmware update", "defaut de connexion E/S": "I/O connection fault",
    "pas de connexion E/S": "no I/O connection", "configuration non valide": "invalid configuration",
    "connecte, au moins une connexion E/S en RUN": "connected, at least one I/O connection in RUN",
    "connecte, connexions E/S en idle": "connected, I/O connections idle", "connexion E/S en defaut": "I/O connection faulted",
    "Automate, modules du chassis 1756 emplacement par emplacement, axes en defaut.": "Controller, 1756 chassis modules slot by slot, faulted axes.",
    "Nom du tag, comme dans Studio 5000 : Recipe_Speed, Recipe[3].Temp, Program:Main.Count. Ecriture : BOOL, entiers, REAL, STRING (pas les tags de securite).":
      "Tag name, as in Studio 5000: Recipe_Speed, Recipe[3].Temp, Program:Main.Count. Writing: BOOL, integers, REAL, STRING (not safety tags).",
    "Mot d'etat (identite)": "Status word (identity)", "Produit": "Product", "Defauts": "Faults", "Mode": "Mode",
    "Axes : automates Allen-Bradley seulement": "Axes: Allen-Bradley controllers only",
    "Fonction non disponible pour un automate Allen-Bradley (programme : Studio 5000)": "Not available for an Allen-Bradley controller (program: Studio 5000)",
    "refuse par l'automate (tag de securite, en lecture seule ou inexistant). Les tags de securite ne s'ecrivent pas de l'exterieur.":
      "refused by the controller (safety tag, read only or missing). Safety tags cannot be written from outside.",
    "nombre attendu (ex. 12.5)": "number expected (e.g. 12.5)", "nombre entier attendu": "integer expected",
    "pycomm3 manque : pip install pycomm3": "pycomm3 missing: pip install pycomm3",
    "Le PC est-il dans le meme reseau que l'automate (meme debut d'adresse) ? Emplacement de la CPU correct ?":
      "Is the PC in the same network as the PLC (same address start)? Correct CPU slot?",
    "Adresse IP de l'automate a renseigner (ex. 192.168.1.10)": "PLC IP address to fill in (e.g. 192.168.1.10)",
    "Emplacement CPU : un nombre": "CPU slot: a number",
    // short words, used inside longer texts
    "entrees": "inputs", "sorties": "outputs", "entree": "input", "sortie": "output", "voies": "channels", "voie": "channel",
    "mots": "words", "octets": "bytes", "octet": "byte", "alerte(s)": "alert(s)", "differents": "different",
    "emplacement(s) en defaut": "faulty slot(s)", "en defaut": "faulty", "dans le rack": "in rack",
    "allume": "on", "eteint": "off", "clignote": "flashing", "decalee": "wrong",
    "relais": "relay", "transistors": "transistors", "analogiques": "analog", "tension": "voltage", "courant": "current",
    "thermocouple": "thermocouple", "sonde": "probe", "de comptage": "counting",
    "mini": "min", "maxi": "max", "connecteurs": "connectors", "integrees": "built-in",
    "dossier": "folder", "Dossier": "Folder", "importe de": "imported from", "faite pour": "made for",
    "station": "station", "niveau": "level", "classe": "class", "zone": "area",
  };

  // ---- messages with numbers or names inside ----
  const PATTERNS = [
    [/^Mis a jour a (.+)$/, "Updated at $1"], [/^Lu a (.+)$/, "Read at $1"],
    [/^Dossier : (.+)$/, "Folder: $1", true], [/^Empl\. (\d+)$/, "Slot $1"], [/^empl\. (\d+) octet (\d+)$/, "slot $1 byte $2"],
    [/^(\d+) pas$/, "$1 steps"], [/^niveau (\d+)(.*)$/, (m, n, rest) => `level ${n}${phrases(rest)}`], [/^(\d+) emplacement\(s\) en defaut dans le rack (\d+)$/, "$1 faulty slot(s) in rack $2"],
    [/^Configuration materielle compilee le (.+)\.$/, "Hardware configuration compiled on $1."],
    [/^Horloge CPU decalee : (.+) \(PC : (.+)\) — les dates du tampon de diagnostic sont fausses$/,
      "CPU clock is off: $1 (PC: $2) — diagnostic buffer dates are wrong"],
    [/^(\S+) allume : (.+)$/, "$1 on: $2"], [/^Copie du (.+) supprimee\.$/, "Copy of $1 deleted."],
    [/^lecture ([A-Z%].*)$/, "reading $1"], [/^chargement ([A-Z%].*)$/, "loading $1"],
    [/^suppression ([A-Z%].*)$/, "deleting $1"], [/^ecriture ([A-Z%].*)$/, "writing $1"],
    [/^(\d+) adresse\(s\) ajoutee\(s\)\.$/, "$1 address(es) added."],
    [/^(\d+) adresse\(s\) importee\(s\) depuis (.+)\.$/, "$1 address(es) imported from $2.", true],
    [/^(.+) remis en automatique \(DHCP\)\.$/, "$1 set back to automatic (DHCP)."],
    [/^(\d+) bloc\(s\) vont etre charges dans la CPU\.$/, "$1 block(s) will be loaded into the CPU."],
    [/^(\d+) (\S+) differents de la copie \(sur (\d+)\)\. Seuls ceux-la seront ecrits :$/, "$1 $2 differ from the copy (out of $3). Only these will be written:"],
    [/^(\d+) (\S+) de la copie n'existent pas dans l'automate \(il a (\d+) (\S+)\) : ignores\.$/, "$1 $2 of the copy do not exist in the PLC (it has $3 $4): skipped."],
    [/^(\d+) %KW de la copie non ecrits \(constantes : restauration pas encore validee\)\.$/, "$1 %KW of the copy not written (constants: restore not validated yet)."],
    [/^(\d+) (\S+) differents apres ecriture \(reecrits par le programme \?\)$/, "$1 $2 different after writing (rewritten by the program?)"],
    [/^(\d+) valeur\(s\) ecrite\(s\) et relue\(s\)\.$/, "$1 value(s) written and read back."],
    [/^(\d+) coupleurs WAGO trouves : choisir\.$/, "$1 WAGO couplers found: choose."],
    [/^\.\.\. et (\d+) autres\.$/, "... and $1 more."],
    [/^Adresse (\S+) reprise : cliquer sur Connecter\.$/, "Address $1 taken: click Connect."],
    [/^Blocs a charger dans la CPU \((\d+)\) :$/, "Blocks to load into the CPU ($1):"],
    [/^Charger les OB vides (.+) dans la CPU \?$/, "Load the empty OBs $1 into the CPU?"],
    [/^Coupleur trouve en (\S+), PC garde (.+) : Connecter\.$/, "Coupler found at $1, PC keeps $2: Connect."],
    [/^Coupleur en (\S+), PC en (.+) : Connecter\.$/, "Coupler at $1, PC at $2: Connect."],
    [/^Ecrire (\d+) (\S+) dans l'automate \?$/, "Write $1 $2 to the PLC?"], [/^Ecrire ces (\d+) (\S+)$/, "Write these $1 $2"],
    [/^Lecture des (\S+) de l'automate\.\.\.$/, "Reading the PLC's $1..."], [/^PC en (\S+) sur (.+)\.$/, "PC at $1 on $2."],
    [/^Restauration terminee : (\d+) (\S+) ecrits et verifies\.$/, "Restore finished: $1 $2 written and checked."],
    [/^Rien a restaurer : les (\d+) (\S+) de l'automate sont deja identiques a la copie\.$/, "Nothing to restore: the PLC's $1 $2 already match the copy."],
    [/^Supprimer du PC la copie du (.+) \?$/, "Delete the copy of $1 from the PC?"],
    [/^Cette copie sera perdue \(l'automate n'est pas touche\)\.$/, "This copy will be lost (the PLC is not touched)."],
    [/^Adresse (\S+) donnee \((.+)\), mais le port 502/, "Address $1 given ($2), but port 502"],
    [/^module (\d+) (entrees|sorties) TOR/, (m, n, k) => `module ${n} digital ${k === "entrees" ? "inputs" : "outputs"}`],
    [/^(\d+) mots \+ (\d+) bits$/, "$1 words + $2 bits"], [/^(\d+) voies$/, "$1 channels"],
    [/^(\d+) (entrees|sorties)( .*)?$/, (m, n, k, rest) => `${n} ${k === "entrees" ? "inputs" : "outputs"}${rest ? " " + phrases(rest.trim()) : ""}`],
    [/^lecture D : (\d+) mots$/, "reading D: $1 words"],
    [/^Chien de garde depasse \(M1008\) : programme trop long ou boucle, au pas D1008 = (.+)$/, "Watchdog exceeded (M1008): program too long or a loop, at step D1008 = $1"],
    [/^Erreur d'execution \(M1067 = (.+?)\) : D1067 = (.+?), au pas D1068 = (.+)$/, "Runtime error (M1067 = $1): D1067 = $2, at step D1068 = $3"],
    [/^Erreur de syntaxe du programme \(M1004\) : D1004 = (.+)$/, "Program syntax error (M1004): D1004 = $1"],
    [/^Supprimer (.+) \?$/, "Delete $1?"],
    [/^Axe (\S+) : (.*)$/, (m, a, rest) => `Axis ${a}: ${phrases(rest)}`],
    [/^Emplacement (\d+) \((.+?)\) : (.*)$/, (m, n, mod, rest) => `Slot ${n} (${mod}): ${phrases(rest)}`],
    [/^Automate : (.*)$/, (m, rest) => `Controller: ${phrases(rest)}`],
    [/^(apparu|disparu) : (.*)$/, (m, k, rest) => `${k === "apparu" ? "appeared" : "cleared"}: ${phrases(rest)}`],
    [/^INHIBIT S(\d+) entree de validation \(enable\) absente$/, "INHIBIT S$1 enable input missing"],
    [/^INIT FLT bit (\d+) (.*)$/, (m, b, rest) => `INIT FLT bit ${b} ${phrases(rest)}`],
    [/^defaut module bit (\d+) (.*)$/, (m, b, rest) => `module fault bit ${b} ${phrases(rest)}`],
    [/^defaut STO cable \(GuardFaults bit (\d+)\)$/, "hardwired STO fault (GuardFaults bit $1)"],
    [/^defaut de securite integre \(AxisSafetyFaults bit (\d+)\)$/, "integrated safety fault (AxisSafetyFaults bit $1)"],
    [/^ALARME ([SM]\d+) ?(.*)$/, (m, c, rest) => `ALARM ${c}${rest ? " " + phrases(rest) : ""}`],
    [/^Pas de reponse de (\S+) : (.*)$/, (m, ip, rest) => `No answer from ${ip}: ${phrases(rest)}`],
    [/^Erreur d'operation \(R9007 \/ R9008\) au pas (\d+) \(DT90017 \/ DT90018\)$/, "Operation error (R9007 / R9008) at step $1 (DT90017 / DT90018)"],
    [/^(\d+)k pas$/, "$1k steps"], [/^Extension (\d)$/, "Expansion $1"],
    [/^extension (\d) (W[XY]\d+)$/, "expansion $1 $2"],
    [/^extension (\d) entree (X\w+)$/, "expansion $1 input $2"], [/^extension (\d) sortie (Y\w+)$/, "expansion $1 output $2"], [/^entree X(\w)$/, "input X$1"], [/^sortie Y(\w)$/, "output Y$1"],
    [/^Recherche sur (\S*) : toutes les vitesses, parites et stations \(environ 1 min 30\)\.\.\.$/, "Searching on $1: every speed, parity and station (about 1.5 min)..."],
    [/^FP0R trouve : (\d+) bauds, (\d) bits, parite (\w), station (\d+)\.(.*)$/, (m, b, n, p, st, rest) => `FP0R found: ${b} baud, ${n} bits, parity ${p}, station ${st}.${phrases(rest)}`],
    [/^Aucune reponse \((\d+) essais\)\. (.*)$/, (m, n, rest) => `No answer (${n} tries). ${translateLine(rest)}`],
    [/^Le port outil repond en Modbus RTU \((\d+) bauds, (\S+), station 1\), (.*)$/, (m, b, f, rest) => `The tool port answers in Modbus RTU (${b} baud, ${f}, station 1), ${phrases(rest)}`],
    [/^Des octets arrivent mais illisibles \((.*?)\) : (.*)$/, (m, l, rest) => `Bytes arrive but unreadable (${l}): ${rest.includes("autre protocole") ? "another protocol on the tool port (general-purpose mode / programmed PC link), or another device on the line." : rest}`], [/^lecture DT : (\d+) \/ (\d+) mots$/, "reading DT: $1 / $2 words"],
  ];

  // ---- phrases replaced inside longer texts, longest first ----
  const keys = Object.keys(EN).map((k) => [norm(k), EN[k]]);
  const exact = new Map(keys);
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // whole texts only: too common to swap inside a sentence
  const EXACT_ONLY = new Set(["aucun", "aucune", "Application", "Cycle", "Station", "station", "classe", "zone",
                              "niveau", "dossier", "Dossier", "Module", "Modules", "Format", "Type", "Date", "Note"]);
  const phraseRe = new RegExp("(?<![\\w%-])(" + keys.map((k) => k[0]).filter((k) => k.length >= 4 && !EXACT_ONLY.has(k))
    .sort((a, b) => b.length - a.length).map(esc).join("|") + ")(?![\\w-])", "g");
  function phrases(s) {
    return s.replace(phraseRe, (m) => exact.get(m) ?? m);
  }

  const cache = new Map();
  function translateLine(line) {
    const m = line.match(/^(\s*)([\s\S]*?)(\s*)$/);
    const core = norm(m[2]);
    if (!core || !/[a-z]/i.test(core)) return line;
    let out = cache.get(core);
    if (out === undefined) {
      out = exact.get(core);
      if (out === undefined) {
        for (const [re, rep, final] of PATTERNS) {
          if (re.test(core)) {
            out = core.replace(re, rep);
            if (!final) out = phrases(out);
            break;
          }
        }
      }
      if (out === undefined) out = phrases(core);
      out = out.replace(/ ([:?;!])(?=\s|$)/g, "$1");        // French spacing before : ? ;
      if (cache.size > 5000) cache.clear();
      cache.set(core, out);
    }
    return m[1] + out + m[3];
  }

  // page text: one text, whatever its line breaks
  const tx = (text) => (lang === "en" && typeof text === "string" && text ? translateLine(text) : text);
  // dialogs: one text per paragraph / line
  function t(text) {
    if (lang !== "en" || typeof text !== "string" || !text) return text;
    return text.split("\n").map(translateLine).join("\n");
  }

  // ---- applying it to the page ----
  const ATTRS = ["title", "placeholder", "aria-label", "label"];
  const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA"]);
  let lang = "fr";
  try { lang = localStorage.getItem("langue") === "en" ? "en" : "fr"; } catch { /* default */ }

  function doText(node) {
    if (!node.parentElement || SKIP.has(node.parentElement.tagName) || node.parentElement.closest("[data-no-i18n]")) return;
    if (node.data !== node.__out) node.__src = node.data;       // new text written by the page
    const out = tx(node.__src);
    node.__out = out;
    if (node.data !== out) node.data = out;
  }

  function doAttrs(e) {
    if (e.closest("[data-no-i18n]")) return;
    e.__src = e.__src || {};
    e.__out = e.__out || {};
    for (const a of ATTRS) {
      if (!e.hasAttribute(a)) continue;
      const v = e.getAttribute(a);
      if (v !== e.__out[a]) e.__src[a] = v;
      const out = tx(e.__src[a]);
      e.__out[a] = out;
      if (v !== out) e.setAttribute(a, out);
    }
  }

  function walk(root) {
    if (root.nodeType === 3) return doText(root);
    if (root.nodeType !== 1 || SKIP.has(root.tagName)) return;
    doAttrs(root);
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let n;
    while ((n = w.nextNode())) n.nodeType === 3 ? doText(n) : doAttrs(n);
  }

  const observer = new MutationObserver((list) => {
    for (const m of list) {
      if (m.type === "characterData") doText(m.target);
      else if (m.type === "attributes") doAttrs(m.target);
      else m.addedNodes.forEach(walk);
    }
  });

  function apply() {
    document.documentElement.lang = lang;
    walk(document.body);
  }

  function setLang(l) {
    lang = l === "en" ? "en" : "fr";
    try { localStorage.setItem("langue", lang); } catch { /* not kept */ }
    apply();
  }

  // dialogs written by app.js
  for (const name of ["alert", "confirm", "prompt"]) {
    const orig = window[name].bind(window);
    window[name] = (msg, ...rest) => orig(t(String(msg ?? "")), ...rest);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const sel = document.getElementById("langue");
    if (sel) {
      sel.value = lang;
      sel.addEventListener("change", () => setLang(sel.value));
    }
    apply();
    observer.observe(document.body, { subtree: true, childList: true, characterData: true,
                                      attributes: true, attributeFilter: ATTRS });
  });

  return { t, setLang, get lang() { return lang; } };
})();
