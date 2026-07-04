from enum import Enum
from typing import Optional
from pydantic import BaseModel


class EntityType(str, Enum):
    MATERIAL = "Material"
    PROCESS = "Process"
    EQUIPMENT = "Equipment"
    PROPERTY = "Property"
    CONDITION = "Condition"
    EXPERIMENT = "Experiment"
    PUBLICATION = "Publication"
    EXPERT = "Expert"
    FACILITY = "Facility"
    CONCLUSION = "Conclusion"
    TOPIC = "Topic"
    ARCHIVE = "Archive"
    PATENT = "Patent"
    STANDARD = "Standard"


class Confidence(str, Enum):
    VERIFIED = "verified"
    PROBABLE = "probable"
    HYPOTHESIS = "hypothesis"


class Geography(str, Enum):
    RU = "RU"
    INTERNATIONAL = "International"
    GLOBAL = "Global"


class Entity(BaseModel):
    id: str
    type: EntityType
    name_ru: str
    name_en: Optional[str] = None
    aliases: list[str] = []
    geography: Optional[str] = None
    year: Optional[int] = None
    confidence: Optional[str] = None
    last_updated: Optional[str] = None
    description: Optional[str] = None

    class Config:
        extra = "allow"


class Relation(BaseModel):
    from_: str
    type: str
    to: str

    class Config:
        extra = "allow"
        populate_by_name = True
