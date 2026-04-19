# Asok Test Suite

Suite de tests unitaires et d'intégration pour le framework **Asok**.  
Tous les tests sont écrits avec [pytest](https://pytest.org) et n'utilisent aucune dépendance externe au framework.

---

## Lancer les tests

```bash
# Depuis la racine du projet
python3 -m pytest tests/

# Avec détails
python3 -m pytest tests/ -v

# Un fichier spécifique
python3 -m pytest tests/test_orm.py

# Un test précis
python3 -m pytest tests/test_orm.py::TestPassword::test_check_password_correct
```

> **Prérequis** : `pip install pytest`

---

## Structure

```
tests/
├── conftest.py           # Fixtures partagées (app, client, tmp_dir)
├── test_templates.py     # Moteur de templates
├── test_validation.py    # Règles de validation
├── test_orm.py           # ORM SQLite
├── test_security.py      # Sécurité (XSS, path traversal, HMAC)
├── test_csrf.py          # Protection CSRF
├── test_routing.py       # Routage et parseur de requêtes (WSGI)
├── test_cache.py         # Cache mémoire et fichier
├── test_background.py    # Tâches en arrière-plan
├── test_ratelimit.py     # Rate limiting
├── test_session.py       # Gestion de sessions
├── test_auth.py          # Authentification et jetons
├── test_forms.py         # Construction et validation de formulaires
├── test_mail.py          # Moteur d'envoi d'e-mails (SMTP)
├── test_logger.py        # Journalisation et middleware de requêtes
├── test_scheduler.py     # Tâches périodiques récurrentes
├── test_component.py     # Composants réactifs et cycle de vie
├── test_utils.py         # Utilitaires (formatage, minification)
├── test_exceptions.py    # Exceptions personnalisées
├── test_websocket.py     # Serveur et connexions WebSocket
└── README.md             # Ce fichier
```

---

## Couverture des modules

### `test_templates.py` — Moteur de templates
| Classe | Tests |
|---|---|
| `TestVariableRendering` | Variables simples, entiers, accès à un dict |
| `TestAutoEscaping` | Balises `<script>` échappées, filtre `safe` |
| `TestHtmlSafeJson` | Échappement de `<`, `>`, `&`, injection `</script>` |
| `TestFilters` | `upper`, `lower`, `length`, `tojson` (XSS-safe) |
| `TestControlFlow` | `{% if %}`, `{% else %}`, `{% for %}`, imbrication |
| `TestSafeString` | `SafeString` non double-échappée |

### `test_validation.py` — Validation
| Classe | Tests |
|---|---|
| `TestRequired` | Champ vide, `None`, clé absente |
| `TestStringRules` | `min`, `max`, `email`, `url`, `alpha`, `alphanumeric`, `regex` |
| `TestNumericRules` | `numeric`, `integer` |
| `TestChainedRules` | Règles chaînées avec `\|`, champs multiples |
| `TestCustomMessages` | Messages d'erreur personnalisés |
| `TestCustomRules` | `register_rule()` + utilisation |

### `test_orm.py` — ORM SQLite
| Classe | Tests |
|---|---|
| `TestCRUD` | `create`, `find(id=x)`, `update`, `delete`, `all`, `count`, `exists` |
| `TestPassword` | Hachage automatique, `check_password(field, plain)` |
| `TestPagination` | Page 1/2, `total`, `current_page` |
| `TestWhere` | `where(column, value).get()` |
| `TestUnique` | Contrainte unique raise exception |
| `TestToDict` | Sérialisation, exclusion du mot de passe |
| `TestPost` | Modèle sans password, valeurs par défaut |

### `test_security.py` — Sécurité
| Classe | Tests |
|---|---|
| `TestXssProtection` | `html_safe_json`, auto-escape variables, filtre `tojson` |
| `TestPathTraversal` | `../..`, `/etc/passwd`, chemin absolu hors base |
| `TestCookieSigning` | Cookie HMAC valide, cookie falsifié rejeté |

### `test_csrf.py` — CSRF
| Classe | Tests |
|---|---|
| `TestCsrfTokenGeneration` | Token est une chaîne |
| `TestCsrfValidation` | `hmac.compare_digest`, token vide, token `None` |
| `TestOriginValidation` | Same-origin accepté, cross-origin rejeté, fallback Referer |

### `test_request.py` — HTTP
| Classe | Tests |
|---|---|
| `TestGet` | Réponse valide, route inexistante |
| `TestResponseObject` | `status_code`, `text`, opérateur `in` |
| `TestJson` | Parsing JSON, `status_code` |
| `TestCookies` | `Set-Cookie` stocké, `Max-Age=0` supprime le cookie |

### `test_cache.py` — Cache
| Classe | Tests |
|---|---|
| `TestMemoryCache` | `set/get`, valeur absente, `forget`, overwrite, TTL, types complexes |
| `TestFileCache` | Même couverture avec backend fichier (temp dir) |

### `test_background.py` — Tâches de fond
| Classe | Tests |
|---|---|
| `TestBackgroundTasks` | Retourne un `Future`, non-bloquant, args, kwargs, tâches multiples, exception silencieuse |

### `test_ratelimit.py` — Rate Limiting
| Classe | Tests |
|---|---|
| `TestRateLimit` | Callable, `max_requests`, `window`, isolation par clé |

### `test_session.py` — Sessions
| Classe | Tests |
|---|---|
| `TestSidGeneration` | Type de jeton (string, hex), longueur (>=32), unicité |
| `TestMemoryStore` / `TestFileStore` | `save`, `load`, `delete`, `ttl_expiry` (mémoire seulement), types imbriqués |
| `TestSessionDict` | Interface Dictionnaire (`get`, `pop`, `setdefault`, `update`, `clear`) |

### `test_auth.py` — Authentification
| Classe | Tests |
|---|---|
| `TestHmacSigning` | Signature HMAC (`sign`/`unsign`), falsification, signature manquante/vide, rejet inter-clés secrètes |
| `TestBearerToken` | Création de token, payload avec expiration, token falsifié, rejet de l'expiration et clés différentes |

### `test_forms.py` — Formulaires
| Classe | Tests |
|---|---|
| `TestFormConstruction` | `dict` de champs, erreurs initiales, clés correspondantes |
| `TestDataBinding` | Liaison des données GET/POST avec fallback vide |
| `TestFormValidation` | Validation (réussite et erreur), génération de l'objet `errors` partiel/multiple |
| `TestFieldTypes` | Tous les types HTML (text, email, password, textarea, number, checkbox, select, hidden, file, url) |
| `TestHtmlRendering` | Méthodes `render` associées aux champs |

### `test_routing.py` — Routage et Requêtes (WSGI)
| Classe | Tests |
|---|---|
| `TestBasicRouting` | Requêtes directes, route valide 1xx-5xx, `status_code`, route inconnue, objet réponse |
| `TestEnvironConstruction` | Création de l'environnement WSGI fictif (Méthodes, paramètres GET, payload POST, Entêtes) |
| `TestRequestObject` | Interface `Request` (method, path, arguments, form data, JSON body, Host Header) |
| `TestHttpVerbs` | Client de test avec toutes les méthodes (`GET`, `POST`, `PUT`, `DELETE`, `PATCH`) |

### `test_mail.py` — E-mails
| Classe | Tests |
|---|---|
| `TestMailDispatch` | Envoi synchrone/asynchrone, fallback de l'expéditeur par défaut, expéditeur personnalisé |
| `TestMailFormatting` | Destinataires multiples, gestion CC/BCC, alternative HTML |
| `TestMailSecurity` | Prévention d'injection d'en-têtes (nettoyage des CRLF dans le Sujet et l'Expéditeur) |

### `test_logger.py` — Logs
| Classe | Tests |
|---|---|
| `TestGetLogger` | Instanciation par nom, niveau personnalisé, format JSON |
| `TestRequestLogger` | Middleware (temps d'exécution, requêtes loggées, neutralisation CRLF dans le `path`) |

### `test_scheduler.py` — Planificateur
| Classe | Tests |
|---|---|
| `TestScheduler` | Exécution récurrente, annulation de tâche, arguments `*args`/`**kwargs`, résilience aux exceptions |

### `test_component.py` — Composants Réactifs
| Classe | Tests |
|---|---|
| `TestComponent` | Inscription automatique via métaclasse, initialisation `kwargs` et `mount()`, suivi `@exposed`, génération et extraction d'état, rendu du wrapper HTML |

### `test_utils.py` — Utilitaires
| Classe | Tests |
|---|---|
| `TestHumanize` | Formatage de taille de fichiers (`file_size`), des entiers avec séparateurs (`intcomma`), des durées (`duration`) et dates (`time_ago`) |
| `TestMinify` | Minification HTML (`minify_html`), suppression d'espaces et de commentaires |

### `test_exceptions.py` — Exceptions
| Classe | Tests |
|---|---|
| `TestExceptions` | Stockage des attributs de `AbortException` (`status`, `message`) et `RedirectException` (`url`, `status`) avec leurs valeurs par défaut |

### `test_websocket.py` — WebSockets
| Classe | Tests |
|---|---|
| `TestConnection` | Initialisation du client (IP, URI, Headers), envoi de texte et de JSON, méthode de fermeture |
| `TestWebSocketServer` | Enregistrement de hooks (on, connect, disconnect), suivi de connexions `_connections`, broadcast ciblé ou global |

---

## Fixtures principales (`conftest.py`)

| Fixture | Portée | Description |
|---|---|---|
| `app` | `session` | App Asok partagée avec DB en mémoire |
| `client` | `session` | `TestClient` lié à l'app partagée |
| `fresh_app` | `function` | App neuve pour chaque test (isolation) |
| `fresh_client` | `function` | `TestClient` lié à la fresh app |
| `tmp_dir` | `function` | Dossier temporaire supprimé après le test |

---

## Notes

- **Base de données** : les tests ORM utilisent un fichier SQLite temporaire par test (`tmp_path`) et appellent `close_connections()` pour isoler les connexions thread-local.
- **Bytecode** : `sys.dont_write_bytecode = True` est activé dans `asok/__init__.py` — aucun `__pycache__` ne sera créé lors des tests.
- **Dépendances** : zéro dépendance externe autre que `pytest`.

---

[← Back to Repository](../README.md) | [Documentation](../docs/README.md)
