from asok import Model, Field


class DocsIndex(Model):
    title = Field.SearchableText()
    slug = Field.String()
    context = Field.SearchableText()