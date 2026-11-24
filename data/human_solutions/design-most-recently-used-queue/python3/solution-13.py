def find_largest_number(numbers):
    largest = numbers[0]
    for num in numbers:
        if num > largest:
            largest = num
    return largest

# Example usage
numbers = [10, 25, 15, 30, 20]
largest_number = find_largest_number(numbers)
print("The largest number is:", largest_number)