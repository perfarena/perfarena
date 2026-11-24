def calculate_sign_of_product(numbers):
    count = 1
    for num in numbers:
        if num < 0:
            count *= -1
        elif num == 0:
            count = 0
    return count