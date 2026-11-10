function maxProfit(prices) {
    const n = prices.length;
    if (n < 2) return 0;

    let total = 0;
    let min = prices[0];

    for (let i = 1; i < n; i++) {
        const profit = prices[i] - min;
        if (profit > 0) {
            total += profit;
            min = prices[i];
        }
    }

    return total;
}