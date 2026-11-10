def count_occurrences(target, numbers):
    count = 0
    for num in numbers:
        if num == target:
            count += 1
    return count

# Example usage
numbers = [338, 338, 338, 101, 101, 101, 1000]
target_number = 338
result = count_occurrences(target_number, numbers)
print(f"The number {target_number} appears {result} times in the list.")