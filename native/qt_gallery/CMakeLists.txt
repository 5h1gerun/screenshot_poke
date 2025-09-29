cmake_minimum_required(VERSION 3.20)
project(qt_gallery LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Qt6 COMPONENTS Widgets Gui REQUIRED)

add_executable(qt_gallery
    main.cpp
)

target_link_libraries(qt_gallery PRIVATE Qt6::Widgets Qt6::Gui)

