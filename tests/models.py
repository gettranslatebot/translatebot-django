"""Test models for modeltranslation integration tests."""

from django.db import models


class Article(models.Model):
    """Test model for translation."""

    title = models.CharField(max_length=200)
    content = models.TextField()
    description = models.CharField(max_length=500, blank=True)

    class Meta:
        app_label = "tests"

    def __str__(self):
        return self.title


class Product(models.Model):
    """Another test model for translation."""

    name = models.CharField(max_length=100)
    description = models.TextField()

    class Meta:
        app_label = "tests"

    def __str__(self):
        return self.name
