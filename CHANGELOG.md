# Changelog

Historique des changements notables de PdfAtelier, par version. Format inspiré de
[Keep a Changelog](https://keepachangelog.com/fr/1.0.0/) ; versionnage inspiré
de [SemVer](https://semver.org/lang/fr/).

## [1.2.0] - 2026-08-27

### Ajouté

- **Rail de navigation** : les onze vues tiennent sans qu'un libellé soit
  tronqué, ce que les onglets horizontaux ne permettaient plus.
- **Menu Aide** dans la colonne de navigation : fiche du logiciel, glossaire,
  communauté, et « Signaler un problème… » qui ouvre la fenêtre de contact.
- **États vides** : une liste vide explique pourquoi elle l'est et propose
  l'action qui la remplit, au lieu de rester muette.
- **Erreurs de saisie en ligne** : le message s'affiche sous le champ fautif,
  marque ce champ, et disparaît dès qu'on le retouche. Plus de fenêtre à
  fermer avant de pouvoir corriger.
- **Barre d'état** : ce qui n'appelle aucune décision (« Termine », « Rien à
  faire », « Sélectionnez d'abord… ») s'y affiche et s'efface tout seul.

### Modifié

- **Refonte visuelle complète**, aux couleurs du site Open Projects Lab :
  thème clair par défaut, accent cyan, thème sombre toujours commutable.
- **Navigation en colonne verticale** à gauche, avec le logo Open Projects Lab,
  à la place du bandeau horizontal.
- **Plus une seule boîte de dialogue dessinée par Windows.** Les 275 boîtes de
  la suite ont été triées une par une sur la question « mérite-t-elle
  d'arrêter l'utilisateur ? », puis réparties sur quatre médias : message
  thémé, barre d'état, erreur en ligne, confirmation thémée.
- **Une confirmation destructrice n'est jamais l'action par défaut** : sur ces
  dialogues, la touche Entrée annule.

### Corrigé

- **Un PDF protégé en AES-256 n'est plus rétrogradé en RC4-128** par
  « Enregistrer sous » ou « Purger les métadonnées ». `pypdf` retombait sur
  son chiffrement historique, cassé et retiré de PDF 2.0, sans un message :
  le fichier ressortait moins bien protégé qu'à l'entrée.
- La fenêtre de contact ne gèle plus l'interface pendant l'envoi.

### Sécurité

- Mise à jour : le flux d'Open Projects Lab d'abord, l'API GitHub en repli, et
  le téléchargement est vérifié par sa taille et son empreinte SHA-256.
- La liste blanche de téléchargement est restreinte aux publications **du
  dépôt**, plus au seul hôte github.com.
- Le plancher `Pillow>=12.3.0` couvre CVE-2026-25990 et CVE-2026-40192.

