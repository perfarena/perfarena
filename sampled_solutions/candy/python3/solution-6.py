def min_candies(ratings):
    n = len(ratings)
    final_values = [1] * n

    # Forward pass
    for i in range(1, n):
        if ratings[i] > ratings[i - 1]:
            final_values[i] = final_values[i - 1] + 1

    # Backward pass
    for i in range(n - 2, -1, -1):
        if ratings[i] > ratings[i + 1]:
            final_values[i] = max(final_values[i], final_values[i + 1] + 1)

    return sum(final_values)

# Test the solution
ratings = [1, 2, 2, 3, 1]
result = min_candies(ratings)
assert result == 7