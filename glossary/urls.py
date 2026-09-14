from django.urls import path, re_path

from . import views

app_name = 'glossary'

urlpatterns = [
    path('', views.term_list, name='list'),
    path('new/', views.term_create, name='term_create'),
    path('lookup/', views.lookup, name='lookup'),
    path('import/', views.import_upload, name='import'),
    path('import/preview/', views.import_preview, name='import_preview'),
    re_path(r'^import/template\.(?P<fmt>csv|xlsx)$', views.import_template, name='import_template'),
    re_path(r'^export\.(?P<fmt>csv|xlsx)$', views.export_terms, name='export'),
    path('<int:pk>/', views.term_detail, name='term_detail'),
    path('<int:pk>/edit/', views.term_edit, name='term_edit'),
    path('<int:pk>/review/', views.term_review, name='term_review'),
    path('<int:pk>/archive/', views.term_archive, name='term_archive'),
    path('order/<int:order_pk>/terms/add/', views.order_term_add, name='order_term_add'),
    path('order/<int:order_pk>/terms/<int:link_pk>/remove/', views.order_term_remove, name='order_term_remove'),
]
