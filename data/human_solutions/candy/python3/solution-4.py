def min_candies(ratings):
    l2r = [1] * len(ratings)
    r2l = [1] * len(ratings)
    
    for i in range(1, len(ratings)):
        if ratings[i] > ratings[i - 1]:
            l2r[i] = l2r[i - 1] + 1
    
    ratings2 = ratings[::-1]
    
    for i in range(1, len(ratings2)):
        if ratings2[i] > ratings2[i - 1]:
            r2l[i] = r2l[i - 1] + 1
    
    r2l = r2l[::-1]
    
    total = sum(max(l2r[i], r2l[i]) for i in range(len(ratings)))
    
    return total

# Test cases
ratings1 = [1, 0, 2]
ratings2 = [1, 2, 2]

print(min_candies(ratings1))  # Output: 5
print(min_candies(ratings2))  # Output: 4