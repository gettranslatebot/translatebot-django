"""Modeltranslation registration for test models."""

from modeltranslation.translator import TranslationOptions, register

from .models import Article, Document, Product


@register(Article)
class ArticleTranslationOptions(TranslationOptions):
    fields = ("title", "content", "description")


@register(Product)
class ProductTranslationOptions(TranslationOptions):
    fields = ("name", "description")


@register(Document)
class DocumentTranslationOptions(TranslationOptions):
    fields = ("name", "attachment")
