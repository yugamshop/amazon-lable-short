from pydantic import BaseModel, Field, model_validator, field_validator

class ProductInput(BaseModel):
    sku: str = Field(default='', max_length=200)
    asin: str = Field(default='', max_length=100)
    fsn: str = Field(default='', max_length=100)
    name: str = Field(min_length=1, max_length=600)
    rack_id: str = Field(default='', max_length=100)
    stock_quantity: int = Field(default=0, ge=0, le=2147483647)
    low_stock_threshold: int = Field(default=5, ge=0, le=2147483647)
    notes: str = Field(default='', max_length=5000)
    aliases: list[str] = Field(default_factory=list, max_length=100)

    @field_validator('sku','asin','fsn','name','rack_id','notes', mode='before')
    @classmethod
    def strip_text(cls, value): return str(value or '').strip()

    @field_validator('aliases')
    @classmethod
    def clean_aliases(cls, values):
        result=[]; seen=set()
        for value in values:
            value=value.strip()
            if len(value)>200: raise ValueError('Each alias must have at most 200 characters')
            if value and value.casefold() not in seen: result.append(value); seen.add(value.casefold())
        return result

    @model_validator(mode='after')
    def identifier_required(self):
        if not (self.sku or self.asin or self.fsn): raise ValueError('Enter at least a SKU, ASIN or FSN')
        return self
