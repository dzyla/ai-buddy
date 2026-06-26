def fibonacci(n):
    """Prints the first n Fibonacci numbers."""
    a, b = 0, 1
    count = 0
    print("Fibonacci Sequence:")
    while count < n:
        print(a, end=" ")
        a, b = b, a + b
        count += 1

if __name__ == "__main__":
    # Print the first 10 numbers
    fibonacci(10)