# Initialize variables
n = len(prices)
profit = 0

# Iterate over the array
for i in range(1, n):
    if prices[i] > prices[i - 1]:
        profit += prices[i] - prices[i - 1]

# Return the total profit
return profit