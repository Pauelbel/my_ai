name: run_pytest
title: Запуск тестов
description: Запускает pytest по пути или маркеру (-m smoke) в папке проекта; возвращает сводку: прошло, упало, имена упавших
args: path: str = "."
libs: pytest

Примеры: path="tests/test_login.py", path="-m smoke", path="tests -k login".
