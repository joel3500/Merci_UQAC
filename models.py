from datetime import datetime
from peewee import BooleanField, ForeignKeyField, Model, CharField, TextField, DateTimeField
from peewee import BooleanField, ForeignKeyField, Model, CharField, TextField, DateTimeField
from database import db


class BaseModel(Model):
    class Meta:
        database = db


class Etablissement(BaseModel):
    code = CharField(unique=True, max_length=80)
    nom = CharField(unique=True, max_length=255)
    pays = CharField(max_length=120)
    type = CharField(max_length=80)
    is_validated = BooleanField(default=True)
    created_at = DateTimeField(default=datetime.utcnow)


class User(BaseModel):
    prenom = CharField(max_length=50)
    nom = CharField(max_length=80, null=True)
    email = CharField(unique=True, max_length=255)
    password_hash = CharField(max_length=255)

    ville = CharField(max_length=120, null=True)
    pays = CharField(max_length=120, null=True)
    etablissement_scolaire = CharField(max_length=180, null=True)
    type_etablissement = CharField(max_length=80, null=True)
    programme = CharField(max_length=120, null=True)

    is_verified = BooleanField(default=False)
    is_admin = BooleanField(default=False)
    is_owner = BooleanField(default=False)

    verification_code = CharField(max_length=10, null=True)
    verification_created_at = DateTimeField(null=True)

    created_at = DateTimeField(default=datetime.utcnow)


class ChatMessage(BaseModel):
    user = ForeignKeyField(User, backref="messages", null=True, on_delete="SET NULL")

    prenom = CharField(max_length=50)
    programme = CharField(max_length=120)
    etablissement_scolaire = CharField(max_length=180, null=True)
    commentaire = TextField()
    created_at = DateTimeField(default=datetime.utcnow)
