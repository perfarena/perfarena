func reverseInteger(x int) int {
    n := x
    if n < 0 {
        n *= -1
    }
    
    a := 0
    for n > 0 {
        remainder := n % 10
        a = a*10 + remainder
        n /= 10
    }
    
    if a > (1<<31) {
        return 0
    }
    
    if x < 0 {
        a *= -1
    }
    
    return a
}