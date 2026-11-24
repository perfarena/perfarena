def calculate_total(n):
    weeks = n // 7
    days = n % 7
    
    lowest = 28
    highest = 28 + 7 * (weeks - 1)
    total = lowest + highest * (weeks - 1) // 2
    
    extra_days = weeks + 1
    total += sum(range(extra_days))
    
    return total

# Example usage
n = 30
result = calculate_total(n)
print(result)