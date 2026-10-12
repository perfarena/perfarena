function calculateMoneyInBank(n) {
    let output = 0;
    let deposit = 1;
    let currentMonday = 1;

    for (let i = 1; i <= n; i++) {
        output += deposit;
        deposit++;

        if (i % 7 === 0) {
            currentMonday++;
            deposit = currentMonday;
        }
    }

    return output;
}