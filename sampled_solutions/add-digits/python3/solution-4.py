def addDigitsRecursive(num):
    if num < 10:
        return num
    else:
        new_num = num // 10 + num % 10
        return addDigitsRecursive(new_num)