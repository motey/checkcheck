This directory contains db logic that deviates from the normal logic.
Usally we want to try to not mix any CRUD scopes. E.g. db.user.UserCRUD should onyl handle user objects not user_auth objects.
If we need to mixin CRUD object we do this in the db/hooks dir to keep it organized seperatly.

e.g. When a new user is created, we want to create some default Checklist labels for this user. Logic like this should actually live in the API business logic. But as UserCRUD.create() is
called from many places (db_init, oidc login, local login,...), we want to centrealize it in the UserCRUD class.