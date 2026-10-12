def max_consecutive_ones(a, k):
    left = 0
    max_window = 0
    zero_count = 0

    for right in range(len(a)):
        if a[right] == 0:
            zero_count += 1

        while zero_count > k:
            if a[left] == 0:
                zero_count -= 1
            left += 1

        max_window = max(max_window, right - left + 1)

    return max_window

# Example usage
a = [0, 0, 0, 1, 1, 0, 1, 1, 0]
k = 1
result = max_consecutive_ones(a, k)
print(result)  # Output: 6