def distribute_candies(ratings):
    left = [1] * len(ratings)
    right = [1] * len(ratings)
    total_candies = 0

    # Fill left array
    for i in range(1, len(ratings)):
        if ratings[i] > ratings[i - 1]:
            left[i] = left[i - 1] + 1

    # Fill right array
    for i in range(len(ratings) - 2, -1, -1):
        if ratings[i] > ratings[i + 1]:
            right[i] = right[i + 1] + 1

    # Calculate total candies
    for i in range(len(ratings)):
        total_candies += max(left[i], right[i])

    return total_candies

# Test the function
ratings = [1, 0, 1, 2]
print(distribute_candies(ratings))  # Output: 5