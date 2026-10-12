// Sort the array based on the difference between costs of city A and city B
Arrays.sort(costs, (a, b) -> (a[0] - a[1]) - (b[0] - b[1]));

int cost = 0;
int n = costs.length;

// Calculate the minimum cost
for (int i = 0; i < n / 2; i++) {
    cost += costs[i][0]; // Add cost for city A
}

for (int i = n / 2; i < n; i++) {
    cost += costs[i][1]; // Add cost for city B
}

return cost;