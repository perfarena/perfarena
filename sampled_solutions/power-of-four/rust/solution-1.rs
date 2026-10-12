fn is_power_of_four(mut n: i32) -> bool {
    if n < 0 {
        return false;
    }
    
    while n > 1 {
        if n % 4 != 0 {
            return false;
        }
        n /= 4;
    }
    
    true
}

fn main() {
    let n = 64;
    println!("{}", is_power_of_four(n)); // Output: true
}