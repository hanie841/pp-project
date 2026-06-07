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
]
