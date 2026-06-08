from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/new/', views.order_create, name='order_create'),
    path('orders/prosecutors/', views.prosecutors_by_prosecution, name='prosecutors_by_prosecution'),
    path('orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('orders/<int:pk>/accept/', views.order_accept, name='order_accept'),
    path('orders/<int:pk>/provide-link/', views.order_provide_link, name='order_provide_link'),
    path('orders/<int:pk>/log/', views.order_log_service, name='order_log_service'),
    path('orders/<int:pk>/approve/', views.order_approve, name='order_approve'),
    path('orders/<int:pk>/certificate/', views.order_certificate, name='order_certificate'),
    path('orders/<int:pk>/pdf/', views.order_pdf, name='order_pdf'),
    path('orders/<int:pk>/certificate/pdf/', views.certificate_pdf, name='certificate_pdf'),
    # Conference
    path('orders/<int:pk>/conference/create/', views.conference_create, name='conference_create'),
    path('orders/<int:pk>/conference/', views.conference_join, name='conference_join'),
    path('orders/<int:pk>/conference/token/', views.conference_token_api, name='conference_token_api'),
    # Contract Manager
    path('orders/<int:pk>/cm-accept/', views.cm_accept_order, name='cm_accept_order'),
    path('orders/<int:pk>/assign/', views.cm_assign_translators, name='cm_assign_translators'),
    # Translator
    path('translator/', views.translator_dashboard, name='translator_dashboard'),
    path('translator/available/', views.translator_available_orders, name='translator_available_orders'),
    path('translator/self-assign/<int:ll_pk>/', views.translator_self_assign, name='translator_self_assign'),
    path('translator/assignment/<int:pk>/accept/', views.assignment_accept, name='assignment_accept'),
    path('translator/assignment/<int:pk>/decline/', views.assignment_decline, name='assignment_decline'),
    path('translator/assignment/<int:pk>/log/', views.translator_log_service, name='translator_log_service'),
]
