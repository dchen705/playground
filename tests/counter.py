from sdk import workflow, step, sleep, init

@step()
def step_one():
    print("Step one completed!")

@step()
def step_two():
    print("Step two completed!")

@workflow()
def dbos_workflow():
    step_one()
    for _ in range(20):
        print("Press Control + C (or Control + \) to stop the app...")
        sleep(1)
    step_two()

if __name__ == "__main__":
    init(name="counter")
    dbos_workflow()