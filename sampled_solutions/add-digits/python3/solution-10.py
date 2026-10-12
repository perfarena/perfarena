def sum_of_digits(num):
    while num > 9:
        result = 0
        while num > 0:
            result += num % 10
            num //= 10
        num = result
    return num