def calculate_absolute_differences(arr):
    total_sum = sum(arr)
    prefix_sum = 0
    result = []

    for i in range(len(arr)):
        left_count = i
        right_count = len(arr) - i - 1

        left_sum = prefix_sum - arr[i] * left_count
        right_sum = total_sum - prefix_sum - arr[i] * right_count

        absolute_diff_sum = abs(left_sum) + abs(right_sum)
        result.append(absolute_diff_sum)

        prefix_sum += arr[i]

    return result

# Example usage
arr = [1, 4, 6, 8, 10]
result = calculate_absolute_differences(arr)
print(result)